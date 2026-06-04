"""Query AST, expression tree, and fluent builder."""

from __future__ import annotations

from .ast import (
    CountQuery,
    DeleteQuery,
    ExistsQuery,
    Include,
    InsertQuery,
    QueryNode,
    RawQuery,
    SelectQuery,
    TextSearch,
    TraverseQuery,
    UpdateQuery,
    UpsertQuery,
    VectorSearch,
)
from .builder import (
    DeleteBuilder,
    Executor,
    QueryBuilder,
    QueryResult,
    TraverseBuilder,
    UpdateBuilder,
    build_insert,
    build_upsert,
)
from .expressions import (
    BooleanOp,
    Comparison,
    DistanceExpr,
    Expression,
    FieldReference,
    Literal,
    Not,
    OrderTerm,
    Param,
    Predicate,
    and_,
    not_,
    or_,
)

__all__ = [
    # expressions
    "Expression",
    "Predicate",
    "FieldReference",
    "Comparison",
    "BooleanOp",
    "Not",
    "Literal",
    "Param",
    "OrderTerm",
    "DistanceExpr",
    "and_",
    "or_",
    "not_",
    # ast
    "QueryNode",
    "SelectQuery",
    "InsertQuery",
    "UpdateQuery",
    "DeleteQuery",
    "UpsertQuery",
    "CountQuery",
    "ExistsQuery",
    "RawQuery",
    "TraverseQuery",
    "Include",
    "VectorSearch",
    "TextSearch",
    # builder
    "QueryBuilder",
    "UpdateBuilder",
    "DeleteBuilder",
    "TraverseBuilder",
    "QueryResult",
    "Executor",
    "build_insert",
    "build_upsert",
]
