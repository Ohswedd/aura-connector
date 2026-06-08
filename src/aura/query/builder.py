"""Fluent, immutable query builder.

Each chained method returns a *new* builder wrapping an updated AST, so builders are
safe to share and compose. Terminal coroutines (``all``, ``one``, ``first``,
``count``, ``exists``, ``execute``, ``stream``) invoke an injected executor — the
builder itself knows nothing about transports or the protocol, keeping the layering
clean.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..errors import AuraNotFoundError, AuraQueryError
from .ast import (
    CountQuery,
    DeleteQuery,
    ExistsQuery,
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
    "DeleteBuilder",
    "Executor",
    "QueryBuilder",
    "QueryResult",
    "TraverseBuilder",
    "UpdateBuilder",
]


@dataclass
class QueryResult:
    """Uniform execution result returned by an :class:`Executor`."""

    rows: list[Any] = field(default_factory=list)
    count: int | None = None
    affected: int | None = None
    request_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
    ) -> QueryBuilder:
        """Exact vector nearest-neighbour search returning the closest ``top_k``."""
        if top_k <= 0:
            raise AuraQueryError("top_k must be positive")
        return self.nearest(field, query_vector, metric=metric, limit=top_k)

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


def build_insert(
    model: type[AuraModel], rows: list[dict[str, Any]], on_conflict: str | None = None
) -> InsertQuery:
    return InsertQuery(model=model.__name__, rows=tuple(rows), on_conflict=on_conflict)


def build_upsert(
    model: type[AuraModel], key: dict[str, Any], values: dict[str, Any]
) -> UpsertQuery:
    return UpsertQuery(model=model.__name__, key=dict(key), values=dict(values))
