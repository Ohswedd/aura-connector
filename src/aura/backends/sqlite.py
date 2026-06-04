"""SQLite backend — the first-class local backend (aiosqlite).

SQLite is fully functional with no external service: connect, schema create/drop, the full
CRUD/filter/order/paginate set, upsert, count/exists, transactions, JSON and vector storage
as JSON text, and paged streaming all run against a local file or an in-memory database. It
is the default integration target for the test suite and the recommended way to try Aura
without standing up a server.

The driver (``aiosqlite``) is imported lazily in :meth:`connect`; if it is missing, an
:class:`~aura.errors.AuraDriverNotInstalledError` names the exact extra to install.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..observability import Metrics
from .capabilities import BackendCapabilities
from .errors import import_driver
from .sql._backend import SQLBackend
from .sql.compiler import SQLStatement
from .sql.dialects import SQLITE

if TYPE_CHECKING:
    import aiosqlite

    from .registry import BackendURL

__all__ = ["SQLiteBackend"]

_SQLITE_CAPABILITIES = BackendCapabilities(
    name="sqlite",
    transactions=True,
    schema_create=True,
    schema_migrations=True,
    json_fields=True,
    relationships=True,
    graph_traversal=False,
    vector_search=False,
    hybrid_search=False,
    full_text_search=False,
    raw_queries=True,
    explain=True,
    server_side_cursors=False,
    native_protocol=False,
    document_queries=False,
    key_value=False,
)


class SQLiteBackend(SQLBackend):
    """Async SQLite backend over ``aiosqlite``."""

    name = "sqlite"

    def __init__(self, url: BackendURL, metrics: Metrics) -> None:
        super().__init__(url, metrics, SQLITE)
        self._conn: aiosqlite.Connection | None = None

    def capabilities(self) -> BackendCapabilities:
        return _SQLITE_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        aiosqlite = import_driver("aiosqlite", backend="sqlite")
        database = self._url.database or ":memory:"
        # isolation_level=None disables the driver's implicit transactions so BEGIN/COMMIT
        # are explicit. It must be passed to connect(): the connection runs in its own
        # thread and cannot be reconfigured from the calling thread afterwards.
        self._conn = await aiosqlite.connect(database, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        conn = self._require_conn()
        async with conn.execute("SELECT 1") as cursor:
            row = await cursor.fetchone()
        return row is not None and row[0] == 1

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            from ..errors import AuraConnectionError

            raise AuraConnectionError("SQLite backend is not connected")
        return self._conn

    # -- driver primitives -------------------------------------------------------
    async def _exec_ddl(self, statement: str) -> None:
        conn = self._require_conn()
        await conn.execute(statement)
        await conn.commit()

    async def _fetchall(self, statement: SQLStatement) -> list[dict[str, Any]]:
        conn = self._require_conn()
        async with conn.execute(statement.sql, statement.params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_scalar(self, statement: SQLStatement) -> Any:
        conn = self._require_conn()
        async with conn.execute(statement.sql, statement.params) as cursor:
            row = await cursor.fetchone()
        return row[0] if row is not None else None

    async def _execute_write(
        self, statement: SQLStatement
    ) -> tuple[int | None, list[dict[str, Any]]]:
        conn = self._require_conn()
        returning = " RETURNING " in statement.sql.upper()
        async with conn.execute(statement.sql, statement.params) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()] if returning else []
            rowcount = cursor.rowcount
        if not self._in_transaction:
            await conn.commit()
        return rowcount, rows

    async def _fetch_raw(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        conn = self._require_conn()
        # Raw statements use SQLite named placeholders (:name) bound from ``params``.
        async with conn.execute(statement, params) as cursor:
            if cursor.description is None:
                if not self._in_transaction:
                    await conn.commit()
                return []
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def _begin_tx(self) -> None:
        await self._require_conn().execute("BEGIN")

    async def _commit_tx(self) -> None:
        await self._require_conn().commit()

    async def _rollback_tx(self) -> None:
        await self._require_conn().rollback()
