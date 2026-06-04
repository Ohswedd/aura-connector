"""Typed expression tree used to build queries safely.

Expressions are immutable nodes that serialize into the canonical Query IR. Operators
on :class:`FieldReference` (``==``, ``<``, ``.in_()``, ``.contains()`` …) build nodes
rather than concatenating strings, so user input can never be injected into a query.

Class-level field access (``User.id``) returns a :class:`FieldReference`; the model
metaclass wires this up through descriptors.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BooleanOp",
    "Comparison",
    "DistanceExpr",
    "Expression",
    "FieldReference",
    "Literal",
    "Not",
    "OrderTerm",
    "Param",
    "Predicate",
    "and_",
    "not_",
    "or_",
]


class Expression(abc.ABC):
    """Base class for every node in the expression tree."""

    @abc.abstractmethod
    def to_ir(self) -> Any:
        """Serialize this node into its canonical Query IR representation."""


class Predicate(Expression):
    """A boolean-valued expression usable as a filter."""

    def __and__(self, other: Predicate) -> BooleanOp:
        return BooleanOp("and", [self, other])

    def __or__(self, other: Predicate) -> BooleanOp:
        return BooleanOp("or", [self, other])

    def __invert__(self) -> Not:
        return Not(self)


@dataclass(frozen=True)
class Literal(Expression):
    """A concrete value supplied at query-build time."""

    value: Any

    def to_ir(self) -> Any:
        return {"kind": "literal", "value": self.value}


@dataclass(frozen=True)
class Param(Expression):
    """A named bind parameter resolved when a prepared query is executed."""

    name: str

    def to_ir(self) -> Any:
        return {"kind": "param", "name": self.name}


def _wrap(value: Any) -> Expression:
    if isinstance(value, Expression):
        return value
    # Comparing a link field against a model instance compares against its primary key.
    # Duck-typed to avoid importing the model layer (which sits above this one).
    pk = getattr(value, "primary_key_value", None)
    if callable(pk):
        return Literal(pk())
    return Literal(value)


@dataclass(frozen=True)
class FieldReference(Expression):
    """A reference to a model field, optionally with a document path.

    ``model`` is the owning model's name and ``name`` the field name. ``path`` holds
    nested document keys, e.g. ``Document.metadata["source"]`` yields ``path=("source",)``.
    """

    model: str
    name: str
    path: tuple[str, ...] = ()
    vector_dim: int | None = None

    @property
    def field_path(self) -> str:
        if self.path:
            return ".".join((self.name, *self.path))
        return self.name

    def to_ir(self) -> Any:
        ir: dict[str, Any] = {"kind": "field", "model": self.model, "field": self.name}
        if self.path:
            ir["path"] = list(self.path)
        return ir

    def __getitem__(self, key: str) -> FieldReference:
        """Descend into a document field, e.g. ``metadata["source"]``."""
        return FieldReference(self.model, self.name, (*self.path, str(key)), self.vector_dim)

    # -- comparison operators build Comparison nodes (not booleans) --------------
    def __eq__(self, other: object) -> Comparison:  # type: ignore[override]
        return Comparison("eq", self, _wrap(other))

    def __ne__(self, other: object) -> Comparison:  # type: ignore[override]
        return Comparison("ne", self, _wrap(other))

    def __lt__(self, other: Any) -> Comparison:
        return Comparison("lt", self, _wrap(other))

    def __le__(self, other: Any) -> Comparison:
        return Comparison("le", self, _wrap(other))

    def __gt__(self, other: Any) -> Comparison:
        return Comparison("gt", self, _wrap(other))

    def __ge__(self, other: Any) -> Comparison:
        return Comparison("ge", self, _wrap(other))

    def __hash__(self) -> int:
        return hash((self.model, self.name, self.path))

    # -- set / pattern / null predicates ----------------------------------------
    def in_(self, values: Any) -> Comparison:
        return Comparison("in", self, Literal(list(values)))

    def not_in(self, values: Any) -> Comparison:
        return Comparison("not_in", self, Literal(list(values)))

    def contains(self, value: Any) -> Comparison:
        return Comparison("contains", self, _wrap(value))

    def startswith(self, value: str) -> Comparison:
        return Comparison("startswith", self, _wrap(value))

    def endswith(self, value: str) -> Comparison:
        return Comparison("endswith", self, _wrap(value))

    def like(self, pattern: str) -> Comparison:
        return Comparison("like", self, _wrap(pattern))

    def is_null(self) -> Comparison:
        return Comparison("is_null", self, Literal(None))

    def is_not_null(self) -> Comparison:
        return Comparison("is_not_null", self, Literal(None))

    def exists(self) -> Comparison:
        """Document-path existence predicate."""
        return Comparison("exists", self, Literal(None))

    # -- ordering ----------------------------------------------------------------
    def asc(self) -> OrderTerm:
        return OrderTerm(self, "asc")

    def desc(self) -> OrderTerm:
        return OrderTerm(self, "desc")

    # -- vector distance expressions ---------------------------------
    def cosine_distance(self, query: Any) -> DistanceExpr:
        return DistanceExpr(self, "cosine", query)

    def l2_distance(self, query: Any) -> DistanceExpr:
        return DistanceExpr(self, "euclidean", query)

    def dot_product(self, query: Any) -> DistanceExpr:
        return DistanceExpr(self, "dot", query)


@dataclass(frozen=True)
class Comparison(Predicate):
    """A binary predicate comparing a field to a value or expression."""

    op: str
    left: Expression
    right: Expression

    def to_ir(self) -> Any:
        return {
            "kind": "compare",
            "op": self.op,
            "left": self.left.to_ir(),
            "right": self.right.to_ir(),
        }


@dataclass(frozen=True)
class BooleanOp(Predicate):
    """An ``and``/``or`` combination of predicates."""

    op: str
    operands: list[Predicate] = field(default_factory=list)

    def to_ir(self) -> Any:
        return {"kind": "bool", "op": self.op, "operands": [o.to_ir() for o in self.operands]}


@dataclass(frozen=True)
class Not(Predicate):
    """Logical negation of a predicate."""

    operand: Predicate

    def to_ir(self) -> Any:
        return {"kind": "not", "operand": self.operand.to_ir()}


@dataclass(frozen=True)
class OrderTerm:
    """A single ordering term: a field plus a direction."""

    field: FieldReference
    direction: str

    def to_ir(self) -> Any:
        return {"field": self.field.field_path, "direction": self.direction}


@dataclass(frozen=True)
class DistanceExpr(Expression):
    """A vector-distance expression between a vector field and a query vector."""

    field: FieldReference
    metric: str
    query: Any

    def to_ir(self) -> Any:
        query = self.query.to_list() if hasattr(self.query, "to_list") else list(self.query)
        return {
            "kind": "distance",
            "field": self.field.name,
            "metric": self.metric,
            "query": query,
        }


def and_(*predicates: Predicate) -> BooleanOp:
    """Combine predicates with logical AND."""
    return BooleanOp("and", list(predicates))


def or_(*predicates: Predicate) -> BooleanOp:
    """Combine predicates with logical OR."""
    return BooleanOp("or", list(predicates))


def not_(predicate: Predicate) -> Not:
    """Negate a predicate."""
    return Not(predicate)
