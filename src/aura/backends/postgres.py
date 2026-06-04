"""PostgreSQL backend (asyncpg).

A production SQL backend. It reuses the shared SQL compiler and schema builder with the
PostgreSQL dialect (``$1`` placeholders, ``RETURNING``, ``INSERT … ON CONFLICT`` upserts)
and supplies asyncpg driver glue. Integration tests are environment-gated on
``AURA_TEST_POSTGRES_DSN`` and skip cleanly when it is unset.

The driver (``asyncpg``) is imported lazily; if it is missing,
:class:`~aura.errors.AuraDriverNotInstalledError` names the extra to install
(``pip install aura-connector[postgres]``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..observability import Metrics
from .capabilities import BackendCapabilities
from .errors import import_driver
from .sql._backend import SQLBackend
from .sql.compiler import SQLStatement
from .sql.dialects import POSTGRES

if TYPE_CHECKING:
    import asyncpg

    from .registry import BackendURL

__all__ = ["PostgresBackend"]

_POSTGRES_CAPABILITIES = BackendCapabilities(
    name="postgres",
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


class PostgresBackend(SQLBackend):
    """Async PostgreSQL backend over ``asyncpg``."""

    name = "postgres"

    def __init__(self, url: BackendURL, metrics: Metrics) -> None:
        super().__init__(url, metrics, POSTGRES)
        self._conn: asyncpg.Connection | None = None

    def capabilities(self) -> BackendCapabilities:
        return _POSTGRES_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        asyncpg = import_driver("asyncpg", backend="postgres")
        self._conn = await asyncpg.connect(
            host=self._url.host or "localhost",
            port=self._url.port or 5432,
            user=self._url.username,
            password=self._url.password,
            database=self._url.database,
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        value = await self._require_conn().fetchval("SELECT 1")
        return bool(value == 1)

    def _require_conn(self) -> asyncpg.Connection:
        if self._conn is None:
            from ..errors import AuraConnectionError

            raise AuraConnectionError("PostgreSQL backend is not connected")
        return self._conn

    # -- driver primitives -------------------------------------------------------
    async def _exec_ddl(self, statement: str) -> None:
        await self._require_conn().execute(statement)

    async def _fetchall(self, statement: SQLStatement) -> list[dict[str, Any]]:
        records = await self._require_conn().fetch(statement.sql, *statement.params)
        return [dict(record) for record in records]

    async def _fetch_scalar(self, statement: SQLStatement) -> Any:
        return await self._require_conn().fetchval(statement.sql, *statement.params)

    async def _execute_write(
        self, statement: SQLStatement
    ) -> tuple[int | None, list[dict[str, Any]]]:
        conn = self._require_conn()
        if " RETURNING " in statement.sql.upper():
            records = await conn.fetch(statement.sql, *statement.params)
            return len(records), [dict(r) for r in records]
        status = await conn.execute(statement.sql, *statement.params)
        return self._rowcount_from_status(status), []

    async def _fetch_raw(self, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        # Raw statements use native PostgreSQL ``$1`` placeholders; values are bound in the
        # order given by ``params`` (dict insertion order).
        records = await self._require_conn().fetch(statement, *list(params.values()))
        return [dict(r) for r in records]

    async def _begin_tx(self) -> None:
        await self._require_conn().execute("BEGIN")

    async def _commit_tx(self) -> None:
        await self._require_conn().execute("COMMIT")

    async def _rollback_tx(self) -> None:
        await self._require_conn().execute("ROLLBACK")

    @staticmethod
    def _rowcount_from_status(status: str) -> int | None:
        # asyncpg returns tags like "INSERT 0 3", "UPDATE 2", "DELETE 1".
        parts = status.split()
        if parts and parts[-1].isdigit():
            return int(parts[-1])
        return None
