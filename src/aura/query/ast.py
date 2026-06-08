"""Query AST nodes.

Each node is an immutable description of an operation that serializes to the canonical
Query IR. The IR is the stable contract between the query layer and the
protocol layer; it is also what ``explain`` returns and what tests snapshot.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field, replace
from typing import Any

from .expressions import OrderTerm, Predicate

__all__ = [
    "CountQuery",
    "DeleteQuery",
    "ExistsQuery",
    "HybridSearch",
    "Include",
    "InsertQuery",
    "QueryNode",
    "RawQuery",
    "SelectQuery",
    "TextRankedSearch",
    "TextSearch",
    "TraverseQuery",
    "UpdateQuery",
    "UpsertQuery",
    "VectorSearch",
]


@dataclass(frozen=True)
class Include:
    """A relationship to load alongside the primary result."""

    relationship: str
    limit: int | None = None
    order_by: tuple[OrderTerm, ...] = ()

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {"link": self.relationship}
        if self.limit is not None:
            ir["limit"] = self.limit
        if self.order_by:
            ir["sort"] = [o.to_ir() for o in self.order_by]
        return ir


@dataclass(frozen=True)
class VectorSearch:
    """Nearest-neighbour search parameters for a vector field."""

    field: str
    query: tuple[float, ...]
    metric: str = "cosine"

    def to_ir(self) -> dict[str, Any]:
        return {"field": self.field, "metric": self.metric, "query": list(self.query)}


@dataclass(frozen=True)
class TextSearch:
    """Lexical search parameters for hybrid retrieval (legacy `text()`)."""

    fields: tuple[str, ...]
    query: str

    def to_ir(self) -> dict[str, Any]:
        return {"fields": list(self.fields), "query": self.query}


@dataclass(frozen=True)
class TextRankedSearch:
    """Ranked full-text (BM25) search parameters for `search_text()`."""

    field: str
    query: str
    operator: str = "or"
    rank: str = "bm25"
    k1: float | None = None
    b: float | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {
            "field": self.field,
            "query": self.query,
            "operator": self.operator,
            "rank": self.rank,
        }
        if self.k1 is not None:
            ir["k1"] = self.k1
        if self.b is not None:
            ir["b"] = self.b
        return ir


@dataclass(frozen=True)
class HybridSearch:
    """Hybrid text-plus-vector search parameters for `search_hybrid()`."""

    text_field: str
    text_query: str
    vector_field: str
    vector: tuple[float, ...]
    top_k: int = 10
    metric: str = "cosine"
    weight_text: float = 0.5
    weight_vector: float = 0.5
    fusion: str = "weighted_sum"
    operator: str = "or"
    k1: float | None = None
    b: float | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {
            "text_field": self.text_field,
            "text_query": self.text_query,
            "vector_field": self.vector_field,
            "vector": list(self.vector),
            "top_k": self.top_k,
            "metric": self.metric,
            "weights": {"text": self.weight_text, "vector": self.weight_vector},
            "fusion": self.fusion,
            "operator": self.operator,
        }
        if self.k1 is not None:
            ir["k1"] = self.k1
        if self.b is not None:
            ir["b"] = self.b
        return ir


class QueryNode(abc.ABC):
    """Base class for top-level query nodes."""

    operation: str = ""

    @abc.abstractmethod
    def to_ir(self) -> dict[str, Any]:
        """Serialize this query node into its canonical Query IR dict."""


@dataclass(frozen=True)
class SelectQuery(QueryNode):
    """A read query: filter, project, order, paginate, include, and search."""

    operation: str = "select"
    model: str = ""
    filters: tuple[Predicate, ...] = ()
    projection: tuple[str, ...] = ()
    includes: tuple[Include, ...] = ()
    order_by: tuple[OrderTerm, ...] = ()
    limit: int | None = None
    offset: int | None = None
    vector: VectorSearch | None = None
    text: TextSearch | None = None
    text_search: TextRankedSearch | None = None
    hybrid: HybridSearch | None = None
    fusion_alpha: float | None = None
    consistency: str = "strong"
    timeout_ms: int | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {"operation": self.operation, "model": self.model}
        if self.filters:
            ir["filters"] = [f.to_ir() for f in self.filters]
        if self.projection:
            ir["projection"] = list(self.projection)
        if self.includes:
            ir["include"] = [i.to_ir() for i in self.includes]
        if self.order_by:
            ir["sort"] = [o.to_ir() for o in self.order_by]
        if self.limit is not None:
            ir["limit"] = self.limit
        if self.offset is not None:
            ir["offset"] = self.offset
        if self.vector is not None:
            ir["vector"] = self.vector.to_ir()
        if self.text is not None:
            ir["text"] = self.text.to_ir()
        if self.text_search is not None:
            ir["text_search"] = self.text_search.to_ir()
        if self.hybrid is not None:
            ir["hybrid"] = self.hybrid.to_ir()
        if self.fusion_alpha is not None:
            ir["fusion"] = {"alpha": self.fusion_alpha}
        ir["consistency"] = self.consistency
        if self.timeout_ms is not None:
            ir["timeout_ms"] = self.timeout_ms
        return ir


@dataclass(frozen=True)
class TraverseQuery(QueryNode):
    """A graph traversal starting from a model, following links with a depth bound."""

    operation: str = "traverse"
    model: str = ""
    start: tuple[Predicate, ...] = ()
    steps: tuple[str, ...] = ()
    filters: tuple[Predicate, ...] = ()
    max_depth: int = 5
    limit: int | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {
            "operation": self.operation,
            "model": self.model,
            "start": [s.to_ir() for s in self.start],
            "steps": list(self.steps),
            "max_depth": self.max_depth,
        }
        if self.filters:
            ir["filters"] = [f.to_ir() for f in self.filters]
        if self.limit is not None:
            ir["limit"] = self.limit
        return ir


@dataclass(frozen=True)
class InsertQuery(QueryNode):
    """Insert one or more rows."""

    operation: str = "insert"
    model: str = ""
    rows: tuple[dict[str, Any], ...] = ()
    on_conflict: str | None = None

    def to_ir(self) -> dict[str, Any]:
        ir: dict[str, Any] = {
            "operation": self.operation,
            "model": self.model,
            "rows": [dict(r) for r in self.rows],
        }
        if self.on_conflict is not None:
            ir["on_conflict"] = self.on_conflict
        return ir


@dataclass(frozen=True)
class UpdateQuery(QueryNode):
    """Update rows matching filters with a set of field assignments."""

    operation: str = "update"
    model: str = ""
    filters: tuple[Predicate, ...] = ()
    assignments: dict[str, Any] = field(default_factory=dict)

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "filters": [f.to_ir() for f in self.filters],
            "set": dict(self.assignments),
        }


@dataclass(frozen=True)
class DeleteQuery(QueryNode):
    """Delete rows matching filters."""

    operation: str = "delete"
    model: str = ""
    filters: tuple[Predicate, ...] = ()

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "filters": [f.to_ir() for f in self.filters],
        }


@dataclass(frozen=True)
class UpsertQuery(QueryNode):
    """Insert or update a row identified by key fields."""

    operation: str = "upsert"
    model: str = ""
    key: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "key": dict(self.key),
            "values": dict(self.values),
        }


@dataclass(frozen=True)
class CountQuery(QueryNode):
    """Count rows matching filters."""

    operation: str = "count"
    model: str = ""
    filters: tuple[Predicate, ...] = ()

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "filters": [f.to_ir() for f in self.filters],
        }


@dataclass(frozen=True)
class ExistsQuery(QueryNode):
    """Test whether any row matches filters."""

    operation: str = "exists"
    model: str = ""
    filters: tuple[Predicate, ...] = ()

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "filters": [f.to_ir() for f in self.filters],
        }


@dataclass(frozen=True)
class RawQuery(QueryNode):
    """A raw AuraQL escape hatch with bound parameters."""

    operation: str = "raw"
    statement: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_ir(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "statement": self.statement,
            "params": dict(self.params),
        }


def with_changes(node: Any, **changes: Any) -> Any:
    """Return a copy of a frozen query node with fields replaced."""
    return replace(node, **changes)
