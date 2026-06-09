"""Fluent, immutable query builder.

Each chained method returns a *new* builder wrapping an updated AST, so builders are
safe to share and compose. Terminal coroutines (``all``, ``one``, ``first``,
``count``, ``exists``, ``execute``, ``stream``) invoke an injected executor — the
builder itself knows nothing about transports or the protocol, keeping the layering
clean.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar, runtime_checkable

from ..errors import AuraNotFoundError, AuraQueryError
from .ast import (
    CountQuery,
    DeleteQuery,
    ExistsQuery,
    GroupBy,
    HybridSearch,
    Include,
    InsertQuery,
    QueryNode,
    SelectQuery,
    TextRankedSearch,
    TextSearch,
    TraverseQuery,
    UpdateQuery,
    UpsertQuery,
    VectorSearch,
)
from .expressions import FieldReference, OrderTerm, Predicate

if TYPE_CHECKING:
    from ..models import AuraModel

__all__ = [
    "AggregateGroup",
    "AggregateResult",
    "DeleteBuilder",
    "Executor",
    "FacetBucket",
    "FacetResult",
    "GroupByResult",
    "HnswOptions",
    "MetricResult",
    "Page",
    "QueryBuilder",
    "QueryProfile",
    "QueryResult",
    "SearchPage",
    "SearchResultPage",
    "TraverseBuilder",
    "UpdateBuilder",
]

_PageItem = TypeVar("_PageItem")


@dataclass
class QueryResult:
    """Uniform execution result returned by an :class:`Executor`."""

    rows: list[Any] = dc_field(default_factory=list)
    count: int | None = None
    affected: int | None = None
    request_id: int | None = None
    metadata: dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class SearchResultPage:
    """One page of a ranked-search pagination (:meth:`QueryBuilder.search_pages`).

    ``rows`` are the hydrated models for this page (ranked order, stable
    cross-page rank); ``cursor`` is the opaque token to fetch the next page, or
    ``None`` when this is the last page.
    """

    rows: list[Any] = dc_field(default_factory=list)
    cursor: str | None = None

    @property
    def has_more(self) -> bool:
        """Whether a further page is available."""
        return self.cursor is not None


@dataclass(frozen=True)
class Page(Generic[_PageItem]):
    """One page of a ranked search, resumable from an externally-held cursor token.

    Unlike :class:`SearchResultPage` (yielded by the in-process pagination loop),
    a :class:`Page` is the standalone, public result of a single page fetch — what
    :meth:`~aura.Client.resume_search` and :meth:`QueryBuilder.page` return — so an
    application can persist :attr:`next_cursor` across processes and resume later.

    * ``items`` — the hydrated models for this page, in ranked order.
    * ``next_cursor`` — the opaque token to fetch the next page, or ``None`` at the
      end. Treat it as opaque: never parse it. Its lifetime is bounded by the
      server, so resume promptly.
    * ``total`` — the total ranked-result count when the server reports one, else
      ``None`` (it is not always available).
    """

    items: list[_PageItem] = dc_field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None

    @property
    def has_more(self) -> bool:
        """Whether a further page is available (a non-``None`` ``next_cursor``)."""
        return self.next_cursor is not None


#: ``SearchPage`` is the ranked-search specialization of the public :class:`Page`.
SearchPage = Page[Any]


@dataclass(frozen=True)
class HnswOptions:
    """Approximate (HNSW) vector-search preview options for ``search_vector`` (AuraDB v1.3.0).

    All index/search parameters are optional positive integers; omit one to let the
    server choose. ``fallback`` controls what happens when an approximate request
    falls below the server's threshold for using the HNSW index:

    * ``"exact"`` (default) — the server transparently runs exact search instead,
      keeping exact search as the correctness baseline.
    * ``"error"`` — the server returns a structured error rather than silently
      falling back.

    Exact search remains the default and the baseline; these options only take
    effect on a server that advertises the approximate-vector preview capability.
    """

    m: int | None = None
    ef_construction: int | None = None
    ef_search: int | None = None
    fallback: Literal["exact", "error"] = "exact"

    def __post_init__(self) -> None:
        if self.fallback not in ("exact", "error"):
            raise AuraQueryError("HnswOptions.fallback must be 'exact' or 'error'")
        for name in ("m", "ef_construction", "ef_search"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AuraQueryError(f"HnswOptions.{name} must be a positive integer")

    def to_ir(self) -> dict[str, Any]:
        """Serialize to the approximate-options dict carried alongside the vector clause."""
        ir: dict[str, Any] = {}
        for name in ("m", "ef_construction", "ef_search"):
            value = getattr(self, name)
            if value is not None:
                ir[name] = value
        ir["fallback"] = self.fallback
        return ir


@dataclass(frozen=True)
class FacetBucket:
    """One terms-facet bucket: a distinct value and its count."""

    value: Any
    count: int


@dataclass(frozen=True)
class FacetResult:
    """The buckets for one faceted field."""

    field: str
    buckets: list[FacetBucket] = dc_field(default_factory=list)
    used_index: bool = False


@dataclass(frozen=True)
class MetricResult:
    """One computed aggregation metric (``count``/``min``/``max``/``avg``)."""

    op: str
    value: Any
    field: str | None = None


@dataclass(frozen=True)
class AggregateGroup:
    """One group of a group-by aggregation (AuraDB v1.3.0).

    ``key`` is the group's scalar value, ``count`` the number of matched rows in
    it, and ``metrics`` the per-group metric values (empty when the aggregate
    requested no metrics). ``metric(op, field=None)`` is a convenience lookup.
    """

    key: Any
    count: int
    metrics: list[MetricResult] = dc_field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AggregateGroup:
        return cls(
            key=data.get("key"),
            count=int(data.get("count", 0)),
            metrics=[
                MetricResult(op=m["op"], value=m.get("value"), field=m.get("field"))
                for m in data.get("metrics", [])
            ],
        )

    def metric(self, op: str, field: str | None = None) -> Any:
        """The value of this group's metric matching ``op`` (and ``field``), or ``None``."""
        for m in self.metrics:
            if m.op == op and (field is None or m.field == field):
                return m.value
        return None


@dataclass(frozen=True)
class GroupByResult:
    """The grouped result of an ``.aggregate().group_by(...)`` query (AuraDB v1.3.0).

    ``groups`` are ordered count-descending, then key-ascending. ``group_count_total``
    is the total number of distinct groups the server found; when it exceeds
    ``len(groups)`` the result was truncated to ``group_limit``.
    """

    field: str
    groups: list[AggregateGroup] = dc_field(default_factory=list)
    group_count_total: int = 0
    group_limit: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupByResult:
        groups = [AggregateGroup.from_dict(g) for g in data.get("groups", [])]
        return cls(
            field=str(data.get("field", "")),
            groups=groups,
            group_count_total=int(data.get("group_count_total", len(groups))),
            group_limit=int(data.get("group_limit", 0)),
        )

    @property
    def truncated(self) -> bool:
        """Whether more groups exist than were returned (``group_count_total`` exceeds them)."""
        return self.group_count_total > len(self.groups)

    def group(self, key: Any) -> AggregateGroup | None:
        """The group whose ``key`` equals ``key``, if present."""
        for g in self.groups:
            if g.key == key:
                return g
        return None


@dataclass(frozen=True)
class QueryProfile:
    """A best-effort, advisory query profile attached to a result (AuraDB v1.3.0).

    Every field is optional: the server populates only what it measured for a given
    read, and an older server omits the profile entirely. Timings are microseconds
    where the server reports them; treat all values as advisory diagnostics, not a
    stable contract.
    """

    plan_id: str | None = None
    planning_us: int | None = None
    execution_us: int | None = None
    rows_scanned: int | None = None
    rows_matched: int | None = None
    rows_returned: int | None = None
    index_used: bool | None = None
    search_mode: str | None = None
    vector_mode: str | None = None
    facet_buckets: int | None = None
    groups_returned: int | None = None
    timeout_checked: bool | None = None
    deadline_ms: int | None = None
    cursor_mode: str | None = None
    warnings: list[str] = dc_field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryProfile:
        def _int(key: str) -> int | None:
            value = data.get(key)
            return (
                int(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )

        def _bool(key: str) -> bool | None:
            value = data.get(key)
            return bool(value) if isinstance(value, bool) else None

        def _str(key: str) -> str | None:
            value = data.get(key)
            return str(value) if isinstance(value, str) else None

        raw_warnings = data.get("warnings")
        warnings = [str(w) for w in raw_warnings] if isinstance(raw_warnings, list) else []
        return cls(
            plan_id=_str("plan_id"),
            planning_us=_int("planning_us"),
            execution_us=_int("execution_us"),
            rows_scanned=_int("rows_scanned"),
            rows_matched=_int("rows_matched"),
            rows_returned=_int("rows_returned"),
            index_used=_bool("index_used"),
            search_mode=_str("search_mode"),
            vector_mode=_str("vector_mode"),
            facet_buckets=_int("facet_buckets"),
            groups_returned=_int("groups_returned"),
            timeout_checked=_bool("timeout_checked"),
            deadline_ms=_int("deadline_ms"),
            cursor_mode=_str("cursor_mode"),
            warnings=warnings,
        )


@dataclass
class AggregateResult:
    """The result of :meth:`QueryBuilder.aggregate`: metrics and terms facets.

    ``metric(op, field=None)`` and ``facet(field)`` are convenience lookups.
    """

    collection: str = ""
    matched: int = 0
    scanned: int = 0
    filter_present: bool = False
    search_scoped: bool = False
    metrics: list[MetricResult] = dc_field(default_factory=list)
    facets: list[FacetResult] = dc_field(default_factory=list)
    #: The group-by result, present only when the query used ``.group_by(...)`` and
    #: the server returned a ``groups`` object; ``None`` otherwise (old responses).
    groups: GroupByResult | None = None
    #: A best-effort query profile, present only when profiling was requested and the
    #: server attached one; ``None`` otherwise.
    profile: QueryProfile | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AggregateResult:
        groups_raw = data.get("groups")
        profile_raw = data.get("profile")
        return cls(
            collection=data.get("collection", ""),
            matched=int(data.get("matched", 0)),
            scanned=int(data.get("scanned", 0)),
            filter_present=bool(data.get("filter_present", False)),
            search_scoped=bool(data.get("search_scoped", False)),
            metrics=[
                MetricResult(op=m["op"], value=m.get("value"), field=m.get("field"))
                for m in data.get("metrics", [])
            ],
            facets=[
                FacetResult(
                    field=f["field"],
                    used_index=bool(f.get("used_index", False)),
                    buckets=[
                        FacetBucket(value=b["value"], count=int(b["count"]))
                        for b in f.get("buckets", [])
                    ],
                )
                for f in data.get("facets", [])
            ],
            groups=GroupByResult.from_dict(groups_raw) if isinstance(groups_raw, dict) else None,
            profile=QueryProfile.from_dict(profile_raw) if isinstance(profile_raw, dict) else None,
        )

    def metric(self, op: str, field: str | None = None) -> Any:
        """The value of the metric matching ``op`` (and ``field``), or ``None``."""
        for m in self.metrics:
            if m.op == op and (field is None or m.field == field):
                return m.value
        return None

    def facet(self, field: str) -> FacetResult | None:
        """The facet result for ``field``, if requested."""
        for f in self.facets:
            if f.field == field:
                return f
        return None


@runtime_checkable
class Executor(Protocol):
    """The execution contract a client provides to builders."""

    async def run(self, node: QueryNode, model: type[AuraModel]) -> QueryResult:
        """Execute a query node and return its result."""
        ...

    def stream(
        self, node: QueryNode, model: type[AuraModel], batch_size: int
    ) -> AsyncIterator[Any]:
        """Stream hydrated rows for a select query."""
        ...

    def search_pages(
        self, node: QueryNode, model: type[AuraModel], page_size: int
    ) -> AsyncIterator[SearchResultPage]:
        """Page a ranked search by stable cursor token."""
        ...

    async def aggregate(self, node: QueryNode, model: type[AuraModel]) -> AggregateResult:
        """Compute aggregations/facets over a collection."""
        ...

    async def resume_page(
        self, node: QueryNode, model: type[AuraModel], page_size: int, cursor: str | None
    ) -> Page[Any]:
        """Fetch a single ranked-search page, optionally resuming from ``cursor``."""
        ...


def _to_vector_tuple(value: Any) -> tuple[float, ...]:
    if hasattr(value, "to_list"):
        return tuple(value.to_list())
    return tuple(float(x) for x in value)


class QueryBuilder:
    """Immutable builder for read and search queries."""

    __slots__ = ("_executor", "_model", "_query")

    def __init__(
        self,
        model: type[AuraModel],
        executor: Executor,
        query: SelectQuery | None = None,
    ) -> None:
        self._model = model
        self._executor = executor
        self._query = query or SelectQuery(model=model.__name__)

    def _clone(self, query: SelectQuery) -> QueryBuilder:
        return QueryBuilder(self._model, self._executor, query)

    @property
    def query(self) -> SelectQuery:
        """The underlying immutable :class:`SelectQuery` AST node."""
        return self._query

    @property
    def model(self) -> type[AuraModel]:
        """The model class this builder queries."""
        return self._model

    # -- filtering ---------------------------------------------------------------
    def where(self, predicate: Predicate) -> QueryBuilder:
        return self._clone(self._replace(filters=(*self._query.filters, predicate)))

    def filter(self, predicate: Predicate) -> QueryBuilder:
        """Alias for :meth:`where`."""
        return self.where(predicate)

    def find(self, **conditions: Any) -> QueryBuilder:
        """Add equality filters from keyword conditions (e.g. ``find(id=1)``)."""
        builder = self
        for name, value in conditions.items():
            ref = FieldReference(self._model.__name__, name)
            builder = builder.where(ref == value)
        return builder

    # -- projection / relationships ---------------------------------------------
    def select(self, *fields: FieldReference | str) -> QueryBuilder:
        names = tuple(f.field_path if isinstance(f, FieldReference) else str(f) for f in fields)
        return self._clone(self._replace(projection=self._query.projection + names))

    def include(
        self,
        relationship: FieldReference | str,
        *,
        limit: int | None = None,
        order_by: Iterable[OrderTerm] | None = None,
    ) -> QueryBuilder:
        rel = relationship.name if isinstance(relationship, FieldReference) else str(relationship)
        inc = Include(
            relationship=rel,
            limit=limit,
            order_by=tuple(order_by or ()),
        )
        return self._clone(self._replace(includes=(*self._query.includes, inc)))

    # -- ordering / pagination ---------------------------------------------------
    def order_by(self, *terms: OrderTerm | FieldReference) -> QueryBuilder:
        resolved = tuple(t if isinstance(t, OrderTerm) else t.asc() for t in terms)
        return self._clone(self._replace(order_by=self._query.order_by + resolved))

    def limit(self, count: int) -> QueryBuilder:
        if count < 0:
            raise AuraQueryError("limit must be non-negative")
        return self._clone(self._replace(limit=count))

    def offset(self, count: int) -> QueryBuilder:
        if count < 0:
            raise AuraQueryError("offset must be non-negative")
        return self._clone(self._replace(offset=count))

    # -- vector / hybrid search --------------------------------------------------
    def nearest(
        self,
        field: FieldReference | str,
        query_vector: Any,
        *,
        metric: str = "cosine",
        limit: int | None = None,
    ) -> QueryBuilder:
        name = field.name if isinstance(field, FieldReference) else str(field)
        if metric not in {"cosine", "euclidean", "dot"}:
            raise AuraQueryError(f"Unsupported vector metric {metric!r}")
        vec = VectorSearch(field=name, query=_to_vector_tuple(query_vector), metric=metric)
        new = self._replace(vector=vec)
        if limit is not None:
            new = self._replace_on(new, limit=limit)
        return self._clone(new)

    def similar_to(
        self, field: FieldReference | str, query_vector: Any, *, metric: str = "cosine"
    ) -> QueryBuilder:
        """Alias for :meth:`nearest` matching the documented search syntax."""
        return self.nearest(field, query_vector, metric=metric)

    def metric(self, name: str) -> QueryBuilder:
        if self._query.vector is None:
            raise AuraQueryError("metric() requires a prior similar_to()/nearest() call")
        vec = VectorSearch(self._query.vector.field, self._query.vector.query, name)
        return self._clone(self._replace(vector=vec))

    def text(self, *fields: FieldReference | str, query: str) -> QueryBuilder:
        names = tuple(f.name if isinstance(f, FieldReference) else str(f) for f in fields)
        return self._clone(self._replace(text=TextSearch(fields=names, query=query)))

    def fusion(self, *, alpha: float) -> QueryBuilder:
        if not 0.0 <= alpha <= 1.0:
            raise AuraQueryError("fusion alpha must be between 0 and 1")
        return self._clone(self._replace(fusion_alpha=alpha))

    # -- v0.5.0 first-class ranked search ----------------------------------------
    def search_text(
        self,
        field: FieldReference | str,
        query: str,
        *,
        rank: str = "bm25",
        operator: str = "or",
        k1: float | None = None,
        b: float | None = None,
        limit: int | None = None,
    ) -> QueryBuilder:
        """Ranked full-text (BM25) search on a single full-text field."""
        name = field.name if isinstance(field, FieldReference) else str(field)
        if not query or not query.strip():
            raise AuraQueryError("search_text query must be a non-empty string")
        if rank not in {"bm25", "term_frequency"}:
            raise AuraQueryError("rank must be 'bm25' or 'term_frequency'")
        if operator not in {"or", "and"}:
            raise AuraQueryError("operator must be 'or' or 'and'")
        ts = TextRankedSearch(field=name, query=query, operator=operator, rank=rank, k1=k1, b=b)
        new = self._replace(text_search=ts)
        if limit is not None:
            new = self._replace_on(new, limit=limit)
        return self._clone(new)

    def search_vector(
        self,
        field: FieldReference | str,
        query_vector: Any,
        *,
        metric: str = "cosine",
        top_k: int = 10,
        approximate: bool | dict[str, int] | HnswOptions | None = None,
    ) -> QueryBuilder:
        """Vector nearest-neighbour search returning the closest ``top_k``.

        Exact by default. Pass ``approximate=True`` (or an :class:`HnswOptions`, or
        a dict of HNSW parameters — ``m``, ``ef_construction``, ``ef_search``, and
        ``fallback``) to opt into AuraDB's approximate (HNSW) **preview** for this
        query; exact search remains the correctness baseline. ``fallback`` selects
        what happens when a request falls below the server's HNSW threshold:
        ``"exact"`` (default) runs exact search, ``"error"`` returns a structured
        error. A server that does not advertise the approximate-vector preview
        capability rejects the request.
        """
        if top_k <= 0:
            raise AuraQueryError("top_k must be positive")
        builder = self.nearest(field, query_vector, metric=metric, limit=top_k)
        if approximate is None or approximate is False:
            return builder
        if isinstance(approximate, HnswOptions):
            ann = approximate.to_ir()
        elif approximate is True:
            ann = {}
        else:
            ann = self._validate_ann_dict(dict(approximate))
        return builder._clone(builder._replace(vector_ann=ann))

    @staticmethod
    def _validate_ann_dict(ann: dict[str, Any]) -> dict[str, Any]:
        allowed_ints = {"m", "ef_construction", "ef_search"}
        out: dict[str, Any] = {}
        for key, value in ann.items():
            if key == "fallback":
                if value not in ("exact", "error"):
                    raise AuraQueryError("approximate fallback must be 'exact' or 'error'")
                out[key] = value
                continue
            if key not in allowed_ints:
                raise AuraQueryError(
                    f"unknown approximate parameter {key!r} "
                    f"(allowed: {sorted(allowed_ints)} plus 'fallback')"
                )
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AuraQueryError(f"approximate parameter {key!r} must be a positive integer")
            out[key] = value
        return out

    def search_hybrid(
        self,
        text_field: FieldReference | str,
        query: str,
        vector_field: FieldReference | str,
        vector: Any,
        *,
        weights: tuple[float, float] = (0.5, 0.5),
        fusion: str = "weighted_sum",
        top_k: int = 10,
        metric: str = "cosine",
        operator: str = "or",
        k1: float | None = None,
        b: float | None = None,
    ) -> QueryBuilder:
        """Hybrid text-plus-vector search fusing BM25 and exact vector signals."""
        tname = text_field.name if isinstance(text_field, FieldReference) else str(text_field)
        vname = vector_field.name if isinstance(vector_field, FieldReference) else str(vector_field)
        if not query or not query.strip():
            raise AuraQueryError("search_hybrid query must be a non-empty string")
        if fusion not in {"weighted_sum", "reciprocal_rank_fusion"}:
            raise AuraQueryError("fusion must be 'weighted_sum' or 'reciprocal_rank_fusion'")
        if operator not in {"or", "and"}:
            raise AuraQueryError("operator must be 'or' or 'and'")
        wt, wv = weights
        if wt < 0 or wv < 0 or (wt == 0 and wv == 0):
            raise AuraQueryError("hybrid weights must be non-negative and not both zero")
        if top_k <= 0:
            raise AuraQueryError("top_k must be positive")
        hs = HybridSearch(
            text_field=tname,
            text_query=query,
            vector_field=vname,
            vector=_to_vector_tuple(vector),
            top_k=top_k,
            metric=metric,
            weight_text=wt,
            weight_vector=wv,
            fusion=fusion,
            operator=operator,
            k1=k1,
            b=b,
        )
        # top_k bounds the result page.
        return self._clone(self._replace_on(self._replace(hybrid=hs), limit=top_k))

    def consistency(self, level: str) -> QueryBuilder:
        if level not in {"strong", "eventual"}:
            raise AuraQueryError("consistency must be 'strong' or 'eventual'")
        return self._clone(self._replace(consistency=level))

    def timeout(self, milliseconds: int) -> QueryBuilder:
        return self._clone(self._replace(timeout_ms=milliseconds))

    # -- terminal operations -----------------------------------------------------
    async def all(self) -> list[Any]:
        result = await self._executor.run(self._query, self._model)
        return result.rows

    async def one(self) -> Any:
        result = await self._executor.run(self._replace_on(self._query, limit=2), self._model)
        if not result.rows:
            raise AuraNotFoundError(
                f"No {self._model.__name__} matched the query",
                context={"model": self._model.__name__},
            )
        if len(result.rows) > 1:
            raise AuraQueryError(
                f"Expected exactly one {self._model.__name__}, found multiple",
                context={"model": self._model.__name__},
            )
        return result.rows[0]

    async def first(self) -> Any | None:
        result = await self._executor.run(self._replace_on(self._query, limit=1), self._model)
        return result.rows[0] if result.rows else None

    async def count(self) -> int:
        node = CountQuery(model=self._model.__name__, filters=self._query.filters)
        result = await self._executor.run(node, self._model)
        return result.count or 0

    async def exists(self) -> bool:
        node = ExistsQuery(model=self._model.__name__, filters=self._query.filters)
        result = await self._executor.run(node, self._model)
        return bool(result.count)

    def stream(self, *, batch_size: int = 1000) -> AsyncIterator[Any]:
        if batch_size <= 0:
            raise AuraQueryError("batch_size must be positive")
        return self._executor.stream(self._query, self._model, batch_size)

    def search_pages(self, *, page_size: int = 50) -> AsyncIterator[SearchResultPage]:
        """Page a ranked search (``search_text``/``search_vector``/``search_hybrid``)
        by stable cursor token. Yields :class:`SearchResultPage` objects until the
        result is exhausted. Requires a ranked clause; ordinary queries should use
        ``.all()`` or ``.stream()``."""
        if page_size <= 0:
            raise AuraQueryError("page_size must be positive")
        ranked = (
            self._query.text_search is not None
            or self._query.vector is not None
            or self._query.hybrid is not None
        )
        if not ranked:
            raise AuraQueryError(
                "search_pages requires a ranked clause "
                "(search_text, search_vector, or search_hybrid)"
            )
        return self._executor.search_pages(self._query, self._model, page_size)

    # -- v0.6.0 aggregations and facets ------------------------------------------
    def facet(self, field: FieldReference | str, *, limit: int | None = None) -> QueryBuilder:
        """Add a terms facet over a scalar ``field`` (top values by count) to an
        ``.aggregate()`` query. ``limit`` bounds the buckets (default 10)."""
        name = field.name if isinstance(field, FieldReference) else str(field)
        if not name:
            raise AuraQueryError("facet field must not be empty")
        if limit is not None and limit <= 0:
            raise AuraQueryError("facet limit must be positive")
        spec: dict[str, Any] = {"field": name}
        if limit is not None:
            spec["limit"] = limit
        return self._clone(self._replace(facets=(*self._query.facets, spec)))

    def group_by(self, field: FieldReference | str, *, limit: int | None = None) -> QueryBuilder:
        """Group an ``.aggregate()`` query by a scalar ``field`` (AuraDB v1.3.0).

        The aggregate's metrics are then computed per group, and the result carries
        a :class:`GroupByResult` on :attr:`AggregateResult.groups`. ``limit`` bounds
        the number of returned groups (server-ordered count-descending, then
        key-ascending); ``None`` lets the server apply its default cap. Requires the
        backend's ``group_by`` capability.
        """
        name = field.name if isinstance(field, FieldReference) else str(field)
        if not name:
            raise AuraQueryError("group_by field must not be empty")
        if limit is not None and limit <= 0:
            raise AuraQueryError("group_by limit must be positive")
        return self._clone(self._replace(group_by=GroupBy(field=name, limit=limit)))

    def profile(self, enabled: bool = True) -> QueryBuilder:
        """Opt into a best-effort query profile on this read (AuraDB v1.3.0).

        When enabled, the server attaches an advisory :class:`QueryProfile`
        (planning/execution timing, rows scanned/matched, search/vector mode, etc.)
        to the result metadata when it can. The profile is best-effort: any or all
        fields may be absent, and an older server omits it. Requires the backend's
        ``query_profile`` capability.
        """
        return self._clone(self._replace(profile=bool(enabled)))

    def aggregate_count(self) -> QueryBuilder:
        """Add a ``count`` metric to an ``.aggregate()`` query."""
        return self._clone(self._replace(metrics=(*self._query.metrics, {"op": "count"})))

    def min(self, field: FieldReference | str) -> QueryBuilder:
        """Add a ``min`` metric over a numeric ``field`` to an ``.aggregate()`` query."""
        return self._add_metric("min", field)

    def max(self, field: FieldReference | str) -> QueryBuilder:
        """Add a ``max`` metric over a numeric ``field`` to an ``.aggregate()`` query."""
        return self._add_metric("max", field)

    def _add_metric(self, op: str, field: FieldReference | str) -> QueryBuilder:
        name = field.name if isinstance(field, FieldReference) else str(field)
        if not name:
            raise AuraQueryError(f"aggregation {op!r} requires a field")
        return self._clone(self._replace(metrics=(*self._query.metrics, {"op": op, "field": name})))

    async def aggregate(self) -> AggregateResult:
        """Execute the accumulated facets/metrics/groups as an aggregation over the
        collection (optionally scoped by a ``search_text`` BM25 candidate set).
        Requires at least one facet, metric, or ``group_by``."""
        if not self._query.facets and not self._query.metrics and self._query.group_by is None:
            raise AuraQueryError(
                "aggregate() requires at least one facet, metric, or group_by "
                "(facet(...), aggregate_count(), min(...), max(...), group_by(...))"
            )
        return await self._executor.aggregate(self._query, self._model)

    # -- v0.7.0 public cursor resume ---------------------------------------------
    def page(self, *, page_size: int = 50, cursor: str | None = None) -> _PageFetch:
        """Fetch one ranked-search page, optionally resuming from a ``cursor`` token.

        Unlike :meth:`search_pages` (which drives the whole pagination loop in
        process), this fetches a single :class:`Page` and returns its
        ``next_cursor`` so an application can persist it and resume later — even in
        a different process — by passing it back as ``cursor``. The token is opaque;
        never parse it, and resume promptly because its lifetime is server-bounded.

        Works for any ranked search (``search_text``/``search_vector`` including the
        approximate preview/``search_hybrid``). For BM25 and hybrid results that
        must stay stable under concurrent writes, page inside a snapshot
        transaction. Requires the backend's ``cursor_resume`` capability.

        Returns an awaitable; ``await builder.page(cursor=tok)`` yields a
        :class:`Page`.
        """
        if page_size <= 0:
            raise AuraQueryError("page_size must be positive")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise AuraQueryError("cursor must be a non-empty opaque token")
        ranked = (
            self._query.text_search is not None
            or self._query.vector is not None
            or self._query.hybrid is not None
        )
        if not ranked:
            raise AuraQueryError(
                "page() requires a ranked clause (search_text, search_vector, or search_hybrid)"
            )
        return _PageFetch(self._executor, self._query, self._model, page_size, cursor)

    def explain(self) -> dict[str, Any]:
        """Return the client-side Query IR."""
        return self._query.to_ir()

    def __await__(self) -> Any:
        return self.one().__await__()

    # -- helpers -----------------------------------------------------------------
    def _replace(self, **changes: Any) -> SelectQuery:
        from dataclasses import replace

        return replace(self._query, **changes)

    @staticmethod
    def _replace_on(query: SelectQuery, **changes: Any) -> SelectQuery:
        from dataclasses import replace

        return replace(query, **changes)


class UpdateBuilder:
    """Builder for update statements: ``client.update(User).where(...).set(...).execute()``."""

    __slots__ = ("_assignments", "_executor", "_filters", "_model")

    def __init__(self, model: type[AuraModel], executor: Executor) -> None:
        self._model = model
        self._executor = executor
        self._filters: tuple[Predicate, ...] = ()
        self._assignments: dict[str, Any] = {}

    def where(self, predicate: Predicate) -> UpdateBuilder:
        clone = UpdateBuilder(self._model, self._executor)
        clone._filters = (*self._filters, predicate)
        clone._assignments = dict(self._assignments)
        return clone

    def set(self, **assignments: Any) -> UpdateBuilder:
        clone = UpdateBuilder(self._model, self._executor)
        clone._filters = self._filters
        clone._assignments = {**self._assignments, **assignments}
        return clone

    @property
    def node(self) -> UpdateQuery:
        return UpdateQuery(
            model=self._model.__name__,
            filters=self._filters,
            assignments=dict(self._assignments),
        )

    async def execute(self) -> int:
        if not self._assignments:
            raise AuraQueryError("update requires at least one set() assignment")
        result = await self._executor.run(self.node, self._model)
        return result.affected or 0


class DeleteBuilder:
    """Builder for delete statements: ``client.delete(User).where(...).execute()``."""

    __slots__ = ("_executor", "_filters", "_model")

    def __init__(self, model: type[AuraModel], executor: Executor) -> None:
        self._model = model
        self._executor = executor
        self._filters: tuple[Predicate, ...] = ()

    def where(self, predicate: Predicate) -> DeleteBuilder:
        clone = DeleteBuilder(self._model, self._executor)
        clone._filters = (*self._filters, predicate)
        return clone

    @property
    def node(self) -> DeleteQuery:
        return DeleteQuery(model=self._model.__name__, filters=self._filters)

    async def execute(self) -> int:
        result = await self._executor.run(self.node, self._model)
        return result.affected or 0


class TraverseBuilder:
    """Builder for graph traversals."""

    __slots__ = ("_executor", "_model", "_query")

    def __init__(
        self, model: type[AuraModel], executor: Executor, query: TraverseQuery | None = None
    ) -> None:
        self._model = model
        self._executor = executor
        self._query = query or TraverseQuery(model=model.__name__)

    def _clone(self, **changes: Any) -> TraverseBuilder:
        from dataclasses import replace

        return TraverseBuilder(self._model, self._executor, replace(self._query, **changes))

    def start(self, predicate: Predicate) -> TraverseBuilder:
        return self._clone(start=(*self._query.start, predicate))

    def out(self, relationship: FieldReference | str) -> TraverseBuilder:
        rel = relationship.name if isinstance(relationship, FieldReference) else str(relationship)
        return self._clone(steps=(*self._query.steps, rel))

    def where(self, predicate: Predicate) -> TraverseBuilder:
        return self._clone(filters=(*self._query.filters, predicate))

    def max_depth(self, depth: int) -> TraverseBuilder:
        if depth < 1:
            raise AuraQueryError("max_depth must be >= 1")
        return self._clone(max_depth=depth)

    def limit(self, count: int) -> TraverseBuilder:
        return self._clone(limit=count)

    def explain(self) -> dict[str, Any]:
        return self._query.to_ir()

    async def all(self) -> list[Any]:
        result = await self._executor.run(self._query, self._model)
        return result.rows


class _PageFetch:
    """Awaitable returned by :meth:`QueryBuilder.page`: fetches one ranked page."""

    __slots__ = ("_cursor", "_executor", "_model", "_page_size", "_query")

    def __init__(
        self,
        executor: Executor,
        query: SelectQuery,
        model: type[AuraModel],
        page_size: int,
        cursor: str | None,
    ) -> None:
        self._executor = executor
        self._query = query
        self._model = model
        self._page_size = page_size
        self._cursor = cursor

    async def _fetch(self) -> Page[Any]:
        return await self._executor.resume_page(
            self._query, self._model, self._page_size, self._cursor
        )

    def __await__(self) -> Any:
        return self._fetch().__await__()


def build_insert(
    model: type[AuraModel], rows: list[dict[str, Any]], on_conflict: str | None = None
) -> InsertQuery:
    return InsertQuery(model=model.__name__, rows=tuple(rows), on_conflict=on_conflict)


def build_upsert(
    model: type[AuraModel], key: dict[str, Any], values: dict[str, Any]
) -> UpsertQuery:
    return UpsertQuery(model=model.__name__, key=dict(key), values=dict(values))
