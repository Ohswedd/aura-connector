"""Compile canonical Query IR into parameterized SQL.

This is the safety-critical core of the SQL backends. It walks the immutable Query IR the
query builder produces and emits a SQL statement plus an ordered list of bound parameters.
**No user value is ever written into the SQL text** — values become placeholders and travel
to the driver out of band — and **every identifier is quoted** by the dialect. The shape of
the statement is fixed by the query structure, not by any value, so a value such as
``"1; DROP TABLE users"`` is bound as a single string literal and can never alter the query.

Operations the connector cannot express in a backend's SQL (vector/hybrid/full-text search,
graph traversal, document-path filters) raise a structured capability or dialect error
rather than being silently approximated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NamedTuple

from ...errors import AuraBackendCapabilityError, AuraDialectError, AuraQueryError
from ...schema.model_schema import FieldSchema, ModelSchema
from .dialects import Dialect
from .parameters import ParameterCollector

__all__ = ["SQLCompiler", "SQLStatement"]


class SQLStatement(NamedTuple):
    """A compiled statement: SQL text plus the ordered bound parameters."""

    sql: str
    params: list[Any]


# Comparison operators that map directly to a SQL binary operator.
_BINARY_OPS = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


class SQLCompiler:
    """Compile Query IR into :class:`SQLStatement` for a given :class:`Dialect`."""

    def __init__(self, dialect: Dialect, schema_for: Callable[[str], ModelSchema]) -> None:
        self._d = dialect
        self._schema_for = schema_for

    # -- dispatch ----------------------------------------------------------------
    def compile(self, ir: dict[str, Any]) -> SQLStatement:
        operation = ir.get("operation", "select")
        handler = {
            "select": self.compile_select,
            "count": self.compile_count,
            "exists": self.compile_exists,
            "insert": self.compile_insert,
            "update": self.compile_update,
            "delete": self.compile_delete,
            "upsert": self.compile_upsert,
        }.get(operation)
        if handler is None:
            if operation == "traverse":
                raise AuraBackendCapabilityError(
                    "Graph traversal is not supported by the SQL backend",
                    context={"backend": self._d.name, "capability": "graph_traversal"},
                )
            raise AuraQueryError(f"Unsupported SQL operation {operation!r}")
        return handler(ir)

    # -- reads -------------------------------------------------------------------
    def compile_select(self, ir: dict[str, Any]) -> SQLStatement:
        self._reject_search(ir)
        schema = self._schema(ir)
        table = self._table(schema)
        params = ParameterCollector(self._d.paramstyle)

        projection = ir.get("projection")
        columns = self._projection_columns(schema, projection) if projection else "*"
        sql = [f"SELECT {columns} FROM {table}"]
        sql.append(self._where(ir.get("filters", []), params))
        sql.append(self._order_by(ir.get("sort", [])))
        sql.append(self._limit_offset(ir.get("limit"), ir.get("offset")))
        return SQLStatement(self._join(sql), params.values)

    def compile_count(self, ir: dict[str, Any]) -> SQLStatement:
        schema = self._schema(ir)
        params = ParameterCollector(self._d.paramstyle)
        sql = [f"SELECT COUNT(*) AS {self._d.quote('count')} FROM {self._table(schema)}"]
        sql.append(self._where(ir.get("filters", []), params))
        return SQLStatement(self._join(sql), params.values)

    def compile_exists(self, ir: dict[str, Any]) -> SQLStatement:
        schema = self._schema(ir)
        params = ParameterCollector(self._d.paramstyle)
        inner = [f"SELECT 1 FROM {self._table(schema)}"]
        inner.append(self._where(ir.get("filters", []), params))
        inner.append("LIMIT 1")
        sql = f"SELECT EXISTS({self._join(inner)}) AS {self._d.quote('exists')}"
        return SQLStatement(sql, params.values)

    # -- mutations ---------------------------------------------------------------
    def compile_insert(self, ir: dict[str, Any]) -> SQLStatement:
        schema = self._schema(ir)
        rows = [self._normalize_row(schema, dict(r)) for r in ir.get("rows", [])]
        if not rows:
            raise AuraQueryError("insert requires at least one row")
        columns = self._insert_columns(schema, rows)
        params = ParameterCollector(self._d.paramstyle)
        col_sql = ", ".join(self._d.quote(c) for c in columns)
        tuples = []
        for row in rows:
            placeholders = [params.add(row.get(c)) for c in columns]
            tuples.append("(" + ", ".join(placeholders) + ")")
        verb, conflict = self._insert_conflict(ir.get("on_conflict"))
        sql = f"{verb} INTO {self._table(schema)} ({col_sql}) VALUES {', '.join(tuples)}"
        if conflict:
            sql += f" {conflict}"
        if self._d.supports_returning:
            sql += " RETURNING *"
        return SQLStatement(sql, params.values)

    def compile_update(self, ir: dict[str, Any]) -> SQLStatement:
        schema = self._schema(ir)
        assignments = self._normalize_row(schema, dict(ir.get("set", {})))
        if not assignments:
            raise AuraQueryError("update requires at least one assignment")
        params = ParameterCollector(self._d.paramstyle)
        set_sql = ", ".join(
            f"{self._d.quote(col)} = {params.add(value)}" for col, value in assignments.items()
        )
        sql = [f"UPDATE {self._table(schema)} SET {set_sql}"]
        sql.append(self._where(ir.get("filters", []), params))
        return SQLStatement(self._join(sql), params.values)

    def compile_delete(self, ir: dict[str, Any]) -> SQLStatement:
        schema = self._schema(ir)
        params = ParameterCollector(self._d.paramstyle)
        sql = [f"DELETE FROM {self._table(schema)}"]
        sql.append(self._where(ir.get("filters", []), params))
        return SQLStatement(self._join(sql), params.values)

    def compile_upsert(self, ir: dict[str, Any]) -> SQLStatement:
        if not self._d.supports_upsert:
            raise AuraBackendCapabilityError(
                f"Backend {self._d.name!r} does not support upsert",
                context={"backend": self._d.name, "capability": "upsert"},
            )
        schema = self._schema(ir)
        key = self._normalize_row(schema, dict(ir.get("key", {})))
        values = self._normalize_row(schema, dict(ir.get("values", {})))
        merged = {**key, **values}
        columns = list(merged)
        params = ParameterCollector(self._d.paramstyle)
        col_sql = ", ".join(self._d.quote(c) for c in columns)
        placeholders = ", ".join(params.add(merged[c]) for c in columns)
        conflict_cols = ", ".join(self._d.quote(c) for c in key)

        if self._d.name == "mysql":
            updates = ", ".join(f"{self._d.quote(c)} = {params.add(values[c])}" for c in values)
            sql = (
                f"INSERT INTO {self._table(schema)} ({col_sql}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {updates or self._noop_update(key)}"
            )
            return SQLStatement(sql, params.values)

        updates = ", ".join(f"{self._d.quote(c)} = {params.add(values[c])}" for c in values)
        if not updates:
            updates = ", ".join(f"{self._d.quote(c)} = {self._d.quote(c)}" for c in key)
        sql = (
            f"INSERT INTO {self._table(schema)} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"
        )
        if self._d.supports_returning:
            sql += " RETURNING *"
        return SQLStatement(sql, params.values)

    # -- predicate / clause helpers ----------------------------------------------
    def _where(self, filters: list[dict[str, Any]], params: ParameterCollector) -> str:
        if not filters:
            return ""
        clauses = [self._predicate(f, params) for f in filters]
        return "WHERE " + " AND ".join(clauses)

    def _predicate(self, pred: dict[str, Any], params: ParameterCollector) -> str:
        kind = pred.get("kind")
        if kind == "bool":
            joiner = " AND " if pred["op"] == "and" else " OR "
            inner = joiner.join(self._predicate(op, params) for op in pred["operands"])
            return f"({inner})"
        if kind == "not":
            return f"(NOT {self._predicate(pred['operand'], params)})"
        if kind == "compare":
            return self._comparison(pred, params)
        raise AuraQueryError(f"Unsupported predicate kind {kind!r}")

    def _comparison(self, pred: dict[str, Any], params: ParameterCollector) -> str:
        op = pred["op"]
        column = self._column(pred["left"])
        value = self._literal(pred["right"])

        if op in _BINARY_OPS:
            if value is None and op == "eq":
                return f"{column} IS NULL"
            if value is None and op == "ne":
                return f"{column} IS NOT NULL"
            return f"{column} {_BINARY_OPS[op]} {params.add(self._adapt(value))}"
        if op == "in":
            return self._in_clause(column, value, params, negate=False)
        if op == "not_in":
            return self._in_clause(column, value, params, negate=True)
        if op == "contains":
            return self._like(column, f"%{self._escape_like(str(value))}%", params)
        if op == "startswith":
            return self._like(column, f"{self._escape_like(str(value))}%", params)
        if op == "endswith":
            return self._like(column, f"%{self._escape_like(str(value))}", params)
        if op == "like":
            return f"{column} LIKE {params.add(value)}"
        if op in {"is_null"}:
            return f"{column} IS NULL"
        if op in {"is_not_null", "exists"}:
            return f"{column} IS NOT NULL"
        raise AuraQueryError(f"Unsupported comparison op {op!r}")

    def _in_clause(
        self, column: str, values: Any, params: ParameterCollector, *, negate: bool
    ) -> str:
        items = list(values) if values is not None else []
        if not items:
            return "1 = 1" if negate else "1 = 0"
        placeholders = ", ".join(params.add(v) for v in items)
        keyword = "NOT IN" if negate else "IN"
        return f"{column} {keyword} ({placeholders})"

    def _like(self, column: str, pattern: str, params: ParameterCollector) -> str:
        return f"{column} LIKE {params.add(pattern)} ESCAPE '\\'"

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _order_by(self, sort: list[dict[str, Any]]) -> str:
        if not sort:
            return ""
        terms = []
        for term in sort:
            field = str(term["field"])
            if "." in field:
                raise AuraDialectError(
                    "Ordering by a document path is not supported by the SQL backend",
                    context={"dialect": self._d.name, "field": field},
                )
            direction = "DESC" if term.get("direction") == "desc" else "ASC"
            terms.append(f"{self._d.quote(field)} {direction}")
        return "ORDER BY " + ", ".join(terms)

    def _limit_offset(self, limit: int | None, offset: int | None) -> str:
        parts = []
        if limit is not None:
            parts.append(f"LIMIT {int(limit)}")
        if offset:
            if limit is None:
                # SQLite/MySQL require a LIMIT before OFFSET; use the dialect's max sentinel.
                sentinel = "LIMIT -1" if self._d.name == "sqlite" else "LIMIT 18446744073709551615"
                parts.append(sentinel)
            parts.append(f"OFFSET {int(offset)}")
        return " ".join(parts)

    # -- field / value helpers ---------------------------------------------------
    def _column(self, field_ir: dict[str, Any]) -> str:
        if field_ir.get("kind") != "field":
            raise AuraQueryError("Expected a field reference on the left of a comparison")
        if field_ir.get("path"):
            raise AuraDialectError(
                "Document-path filters are not supported by the SQL backend",
                context={"dialect": self._d.name, "field": field_ir.get("field")},
            )
        return self._d.quote(str(field_ir["field"]))

    def _literal(self, expr: dict[str, Any]) -> Any:
        kind = expr.get("kind")
        if kind == "literal":
            return expr["value"]
        if kind == "param":
            raise AuraQueryError("Unbound query parameter encountered in SQL compilation")
        raise AuraQueryError(f"Unsupported value expression {kind!r}")

    def _adapt(self, value: Any) -> Any:
        """Adapt a Python value to a driver-bindable scalar (JSON-encode containers)."""
        if isinstance(value, (list, dict)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return value

    def _normalize_row(self, schema: ModelSchema, row: dict[str, Any]) -> dict[str, Any]:
        """Flatten link references to FK scalars, drop collections, JSON-encode containers."""
        relationships = {r.name: r for r in schema.relationships}
        out: dict[str, Any] = {}
        for key, value in row.items():
            rel = relationships.get(key)
            if rel is not None:
                if rel.kind == "collection":
                    continue
                out[key] = self._link_value(rel.target, value)
                continue
            out[key] = self._adapt(value)
        return out

    def _link_value(self, target: str, value: Any) -> Any:
        if isinstance(value, dict):
            from ...models import get_model

            model = get_model(target)
            pk = model.__aura_schema__.primary_key if model is not None else None
            return value.get(pk) if pk else None
        return value

    def _insert_columns(self, schema: ModelSchema, rows: list[dict[str, Any]]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        candidate = [f.name for f in schema.fields]
        candidate += [r.name for r in schema.relationships if r.kind == "link"]
        present = {key for row in rows for key in row}
        for name in candidate:
            if name in present and name not in seen:
                ordered.append(name)
                seen.add(name)
        for row in rows:
            for key in row:
                if key not in seen:
                    ordered.append(key)
                    seen.add(key)
        return ordered

    def _insert_conflict(self, on_conflict: str | None) -> tuple[str, str]:
        """Return ``(insert_verb, conflict_clause)`` for an on-conflict mode."""
        if on_conflict is None:
            return "INSERT", ""
        if on_conflict == "ignore":
            if self._d.name == "mysql":
                return "INSERT IGNORE", ""
            return "INSERT", "ON CONFLICT DO NOTHING"
        if on_conflict == "replace":
            if self._d.name == "sqlite":
                return "INSERT OR REPLACE", ""
            if self._d.name == "mysql":
                return "REPLACE", ""
            # PostgreSQL has no blanket replace without a conflict target; the upsert path
            # handles targeted replacement. Surface that clearly instead of guessing.
            raise AuraDialectError(
                "on_conflict='replace' requires upsert() on PostgreSQL; use db.upsert(...)",
                context={"dialect": self._d.name},
            )
        raise AuraQueryError(f"Unsupported on_conflict mode {on_conflict!r}")

    def _noop_update(self, key: dict[str, Any]) -> str:
        col = next(iter(key), None)
        if col is None:  # pragma: no cover - upsert always has a key
            raise AuraQueryError("upsert requires a key")
        return f"{self._d.quote(col)} = {self._d.quote(col)}"

    def _projection_columns(self, schema: ModelSchema, projection: list[str]) -> str:
        names = list(projection)
        if schema.primary_key and schema.primary_key not in names:
            names.append(schema.primary_key)
        quoted = []
        for name in names:
            if "." in name:
                raise AuraDialectError(
                    "Projecting a document path is not supported by the SQL backend",
                    context={"dialect": self._d.name, "field": name},
                )
            quoted.append(self._d.quote(name))
        return ", ".join(quoted)

    def _reject_search(self, ir: dict[str, Any]) -> None:
        if "vector" in ir:
            raise AuraBackendCapabilityError(
                "Vector search is not supported by the SQL backend",
                context={"backend": self._d.name, "capability": "vector_search"},
            )
        if "text" in ir:
            raise AuraBackendCapabilityError(
                "Full-text/hybrid search is not supported by the SQL backend",
                context={"backend": self._d.name, "capability": "full_text_search"},
            )

    def _schema(self, ir: dict[str, Any]) -> ModelSchema:
        model = ir.get("model")
        if not model:
            raise AuraQueryError("Query is missing a target model")
        return self._schema_for(str(model))

    def _table(self, schema: ModelSchema) -> str:
        return self._d.quote(schema.name)

    @staticmethod
    def _join(parts: list[str]) -> str:
        return " ".join(part for part in parts if part)

    def json_columns(self, schema: ModelSchema) -> frozenset[str]:
        """Return the set of column names stored as JSON text (decoded on read)."""
        cols = {f.name for f in schema.fields if self._d.is_json_type(f)}
        return frozenset(cols)

    @staticmethod
    def field_is_json(field: FieldSchema, dialect: Dialect) -> bool:
        return dialect.is_json_type(field)
