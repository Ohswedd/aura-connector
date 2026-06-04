"""Shared SQL backend behaviour.

:class:`SQLBackend` holds the dialect-independent half of every SQL adapter: it owns the
model-schema registry, builds the compiler and schema builder, and turns a compiled
:class:`SQLStatement` plus a couple of driver primitives into a :class:`BackendResult`.
Concrete adapters (SQLite, PostgreSQL, MySQL/MariaDB) supply only their driver glue —
connection lifecycle, the two execution primitives, transactions, ping, and raw — so the
query-compilation and result-interpretation logic is written and tested once.
"""

from __future__ import annotations

import abc
import contextlib
import json
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from ...errors import AuraConstraintError, AuraError, AuraSchemaError
from ...models import get_model
from ...observability import Metrics
from ...schema.migrations import MigrationPlan, diff_schemas
from ...schema.model_schema import ModelSchema
from ..base import Backend, BackendResult
from .compiler import SQLCompiler, SQLStatement
from .dialects import Dialect
from .schema import SchemaBuilder

if TYPE_CHECKING:
    from ...models import AuraModel
    from ..registry import BackendURL

__all__ = ["SQLBackend"]


class SQLBackend(Backend):
    """Common SQL execution logic shared by the relational adapters."""

    def __init__(self, url: BackendURL, metrics: Metrics, dialect: Dialect) -> None:
        self._url = url
        self._metrics = metrics
        self._dialect = dialect
        self._schemas: dict[str, ModelSchema] = {}
        self._compiler = SQLCompiler(dialect, self._schema_for)
        self._schema_builder = SchemaBuilder(dialect)
        self._in_transaction = False

    # -- schema registry ---------------------------------------------------------
    def _schema_for(self, model: str) -> ModelSchema:
        schema = self._schemas.get(model)
        if schema is not None:
            return schema
        registered = get_model(model)
        if registered is not None:
            return registered.__aura_schema__
        raise AuraSchemaError(
            f"Model {model!r} is not registered with this backend",
            context={"model": model, "backend": self.name},
        )

    def _register(self, models: Iterable[type[AuraModel]]) -> list[ModelSchema]:
        schemas = [m.__aura_schema__ for m in models]
        for schema in schemas:
            self._schemas[schema.name] = schema
        return schemas

    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for schema in self._register(models):
            for statement in self._schema_builder.create_table(schema):
                await self._exec_ddl(statement)

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        for schema in self._register(models):
            for statement in self._schema_builder.drop_table(schema):
                await self._exec_ddl(statement)

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        old = {"models": [s.to_dict() for s in self._schemas.values()]}
        new = {"models": [m.__aura_schema__.to_dict() for m in models]}
        return diff_schemas(old, new)

    # -- execution ---------------------------------------------------------------
    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        try:
            return await self._execute_query(ir)
        except Exception as exc:
            raise self._translate(exc) from exc

    async def _execute_query(self, ir: dict[str, Any]) -> BackendResult:
        operation = ir.get("operation", "select")
        if operation == "raw":
            return await self._execute_raw(ir)
        statement = self._compiler.compile(ir)
        if operation == "count":
            value = await self._fetch_scalar(statement)
            return BackendResult(count=int(value or 0))
        if operation == "exists":
            value = await self._fetch_scalar(statement)
            return BackendResult(count=1 if value else 0)
        schema = self._schema_for(str(ir["model"]))
        rows = self._decode_rows(schema, await self._fetchall(statement))
        return BackendResult(rows=rows, count=len(rows))

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        try:
            if ir.get("operation") == "upsert":
                return await self._upsert(ir)
            statement = self._compiler.compile(ir)
            schema = self._schema_for(str(ir["model"]))
            rowcount, returning = await self._execute_write(statement)
            rows = self._decode_rows(schema, returning)
            affected = len(rows) if rows and rowcount in (None, -1) else rowcount
            return BackendResult(rows=rows, affected=affected)
        except Exception as exc:
            raise self._translate(exc) from exc

    def _translate(self, exc: Exception) -> BaseException:
        """Map a driver exception to the Aura taxonomy (constraint violations especially)."""
        if isinstance(exc, AuraError):
            return exc
        name = type(exc).__name__
        message = str(exc)
        lowered = message.lower()
        if (
            "IntegrityError" in name
            or "UniqueViolation" in name
            or "DuplicateKey" in name
            or "unique constraint" in lowered
            or "duplicate" in lowered
        ):
            return AuraConstraintError(message, context={"backend": self.name})
        return exc

    async def _upsert(self, ir: dict[str, Any]) -> BackendResult:
        """Update-by-key when the row exists, else insert the merged row.

        Existence is decided with an explicit ``EXISTS`` probe rather than the row count of
        the UPDATE: MySQL reports *changed* rows, so an idempotent update (same values) would
        otherwise look like "no match" and fall through to a duplicate insert. Unlike a single
        ``INSERT … ON CONFLICT``, this does not require every NOT NULL column to be supplied
        when the target row already exists — only the columns being changed.
        """
        model = str(ir["model"])
        schema = self._schema_for(model)
        key = dict(ir.get("key", {}))
        values = dict(ir.get("values", {}))
        filters = [self._eq_predicate(field, value) for field, value in key.items()]

        exists_ir = {"operation": "exists", "model": model, "filters": filters}
        already = await self._fetch_scalar(self._compiler.compile(exists_ir))

        if already:
            if values:
                update_ir = {
                    "operation": "update",
                    "model": model,
                    "filters": filters,
                    "set": values,
                }
                await self._execute_write(self._compiler.compile(update_ir))
            select = self._compiler.compile(self._select_ir(model, filters))
            rows = self._decode_rows(schema, await self._fetchall(select))
            return BackendResult(rows=rows, affected=1)

        insert_ir = {"operation": "insert", "model": model, "rows": [{**key, **values}]}
        _rowcount, returning = await self._execute_write(self._compiler.compile(insert_ir))
        rows = self._decode_rows(schema, returning)
        if not rows:
            select = self._compiler.compile(self._select_ir(model, filters))
            rows = self._decode_rows(schema, await self._fetchall(select))
        return BackendResult(rows=rows, affected=1)

    @staticmethod
    def _eq_predicate(field: str, value: Any) -> dict[str, Any]:
        return {
            "kind": "compare",
            "op": "eq",
            "left": {"kind": "field", "field": field},
            "right": {"kind": "literal", "value": value},
        }

    @staticmethod
    def _select_ir(model: str, filters: list[dict[str, Any]]) -> dict[str, Any]:
        return {"operation": "select", "model": model, "filters": filters, "limit": 1}

    async def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream rows by walking ``LIMIT``/``OFFSET`` windows (paged streaming).

        SQL backends without server-side cursors page the result set: each window holds at
        most ``batch_size`` rows in memory. Iteration stops on the first short page.
        """
        schema = self._schema_for(str(ir["model"]))
        offset = int(ir.get("offset") or 0)
        remaining = ir.get("limit")
        while True:
            window = batch_size if remaining is None else min(batch_size, int(remaining))
            if window <= 0:
                return
            page_ir = {**ir, "limit": window, "offset": offset}
            rows = self._decode_rows(schema, await self._fetchall(self._compiler.compile(page_ir)))
            for row in rows:
                yield row
            if len(rows) < window:
                return
            offset += window
            if remaining is not None:
                remaining = int(remaining) - len(rows)
                if remaining <= 0:
                    return

    # -- raw ---------------------------------------------------------------------
    async def _execute_raw(self, ir: dict[str, Any]) -> BackendResult:
        statement = str(ir.get("statement", ""))
        params = dict(ir.get("params", {}))
        rows = await self._fetch_raw(statement, params)
        return BackendResult(rows=rows, count=len(rows))

    # -- JSON decode -------------------------------------------------------------
    def _decode_rows(self, schema: ModelSchema, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        json_cols = self._compiler.json_columns(schema)
        if not json_cols:
            return rows
        decoded: list[dict[str, Any]] = []
        for row in rows:
            new = dict(row)
            for col in json_cols:
                value = new.get(col)
                if isinstance(value, (str, bytes)):
                    with contextlib.suppress(ValueError, TypeError):
                        new[col] = json.loads(value)
            decoded.append(new)
        return decoded

    # -- transactions ------------------------------------------------------------
    async def begin(self, txid: int, isolation: str) -> None:
        await self._begin_tx()
        self._in_transaction = True

    async def commit(self, txid: int, isolation: str) -> None:
        if self._in_transaction:
            await self._commit_tx()
            self._in_transaction = False

    async def rollback(self, txid: int, isolation: str) -> None:
        if self._in_transaction:
            await self._rollback_tx()
            self._in_transaction = False

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "dialect": self._dialect.name,
            "database": self._url.database,
        }

    # -- driver primitives (implemented by concrete adapters) --------------------
    @abc.abstractmethod
    async def _exec_ddl(self, statement: str) -> None: ...

    @abc.abstractmethod
    async def _fetchall(self, statement: SQLStatement) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def _fetch_scalar(self, statement: SQLStatement) -> Any: ...

    @abc.abstractmethod
    async def _execute_write(
        self, statement: SQLStatement
    ) -> tuple[int | None, list[dict[str, Any]]]: ...

    @abc.abstractmethod
    async def _fetch_raw(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def _begin_tx(self) -> None: ...

    @abc.abstractmethod
    async def _commit_tx(self) -> None: ...

    @abc.abstractmethod
    async def _rollback_tx(self) -> None: ...
