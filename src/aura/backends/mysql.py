"""MySQL / MariaDB backend (aiomysql).

A production SQL backend. It reuses the shared SQL compiler and schema builder with the
MySQL dialect (``%s`` placeholders, backtick identifiers, ``ON DUPLICATE KEY UPDATE``
upserts) and supplies aiomysql driver glue. MySQL/MariaDB lack ``RETURNING`` in the portable
subset, so inserts report affected-row counts rather than echoing inserted rows.

Integration tests are gated on ``AURA_TEST_MYSQL_DSN`` and skip cleanly when unset. The
driver (``aiomysql``) is imported lazily; a missing driver raises
:class:`~aura.errors.AuraDriverNotInstalledError` with the install command.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from ..observability import Metrics
from .capabilities import BackendCapabilities
from .errors import import_driver
from .sql._backend import SQLBackend
from .sql.compiler import SQLStatement
from .sql.dialects import MYSQL

if TYPE_CHECKING:
    import aiomysql

    from .registry import BackendURL

__all__ = ["MySQLBackend"]

_MYSQL_CAPABILITIES = BackendCapabilities(
    name="mysql",
    transactions=True,
    schema_create=True,
    schema_migrations=True,
    json_fields=True,
    relationships=True,
    graph_traversal=False,
    vector_search=False,
    hybrid_search=False,
    full_text_search=True,
    raw_queries=True,
    explain=True,
    server_side_cursors=True,
    native_protocol=False,
    document_queries=False,
    key_value=False,
)


class MySQLBackend(SQLBackend):
    """Async MySQL/MariaDB backend over ``aiomysql``."""

    name = "mysql"

    def __init__(self, url: BackendURL, metrics: Metrics) -> None:
        super().__init__(url, metrics, MYSQL)
        self._conn: aiomysql.Connection | None = None

    def capabilities(self) -> BackendCapabilities:
        return _MYSQL_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        aiomysql = import_driver("aiomysql", backend="mysql")
        self._conn = await aiomysql.connect(
            host=self._url.host or "localhost",
            port=self._url.port or 3306,
            user=self._url.username,
            password=self._url.password or "",
            db=self._url.database,
            autocommit=True,
        )

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        conn = self._require_conn()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()
        return bool(row) and row[0] == 1

    def _require_conn(self) -> aiomysql.Connection:
        if self._conn is None:
            from ..errors import AuraConnectionError

            raise AuraConnectionError("MySQL backend is not connected")
        return self._conn

    # -- driver primitives -------------------------------------------------------
    async def _exec_ddl(self, statement: str) -> None:
        conn = self._require_conn()
        # MySQL/MariaDB raise a note (surfaced as a Python Warning by aiomysql) when a
        # ``CREATE TABLE IF NOT EXISTS`` targets an existing table. That is the expected,
        # idempotent outcome, so suppress the benign note rather than letting it escalate
        # under ``filterwarnings = error``.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*already exists.*")
            async with conn.cursor() as cursor:
                await cursor.execute(statement)

    async def _fetchall(self, statement: SQLStatement) -> list[dict[str, Any]]:
        import aiomysql

        conn = self._require_conn()
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(statement.sql, statement.params)
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_scalar(self, statement: SQLStatement) -> Any:
        conn = self._require_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(statement.sql, statement.params)
            row = await cursor.fetchone()
        return row[0] if row else None

    async def _execute_write(
        self, statement: SQLStatement
    ) -> tuple[int | None, list[dict[str, Any]]]:
        conn = self._require_conn()
        async with conn.cursor() as cursor:
            await cursor.execute(statement.sql, statement.params)
            rowcount = cursor.rowcount
        return rowcount, []

    async def _fetch_raw(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        import aiomysql

        # Raw statements use native MySQL ``%s`` placeholders, bound in dict order.
        conn = self._require_conn()
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(statement, list(params.values()))
            if cursor.description is None:
                return []
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def _begin_tx(self) -> None:
        await self._require_conn().begin()

    async def _commit_tx(self) -> None:
        await self._require_conn().commit()

    async def _rollback_tx(self) -> None:
        await self._require_conn().rollback()
