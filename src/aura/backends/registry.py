"""DSN-based backend routing.

:func:`resolve_backend` turns a DSN into a ``(ClientConfig, Backend)`` pair. It maps the
scheme to a backend family, parses the connection URL, and constructs the matching adapter.
No database driver is imported here: routing a DSN never requires the target's driver to be
installed. Drivers are imported lazily inside each adapter's :meth:`connect`, which raises an
actionable :class:`~aura.errors.AuraDriverNotInstalledError` if the package is missing.

Scheme map:

======================================  ==========================================
Scheme(s)                               Backend
======================================  ==========================================
``aura`` / ``auras`` / ``aura+tcp``     AuraDB (Aura Wire Protocol over TCP/TLS)
``aura+memory`` / ``memory``            in-process reference engine
``sqlite`` / ``sqlite+aiosqlite``       SQLite (aiosqlite)
``postgres`` / ``postgresql``           PostgreSQL (asyncpg)
``postgresql+asyncpg``
``mysql`` / ``mysql+aiomysql``          MySQL / MariaDB (aiomysql)
``mariadb`` / ``mariadb+aiomysql``
``mongodb`` / ``mongodb+motor``         MongoDB (motor)
``redis`` / ``redis+asyncio``           Redis (redis.asyncio) — limited
======================================  ==========================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

from ..config import ClientConfig, parse_dsn
from ..errors import AuraError
from ..observability import Metrics, _TelemetryBridge
from .base import Backend

if TYPE_CHECKING:
    from ..transport.base import Transport

__all__ = ["BackendURL", "SCHEME_FAMILY", "resolve_backend", "backend_family"]

#: Maps each supported DSN scheme to its backend family name.
SCHEME_FAMILY: dict[str, str] = {
    "aura": "auradb",
    "auras": "auradb",
    "aura+tcp": "auradb",
    "aura+memory": "memory",
    "memory": "memory",
    "sqlite": "sqlite",
    "sqlite+aiosqlite": "sqlite",
    "postgres": "postgres",
    "postgresql": "postgres",
    "postgresql+asyncpg": "postgres",
    "mysql": "mysql",
    "mysql+aiomysql": "mysql",
    "mariadb": "mysql",
    "mariadb+aiomysql": "mysql",
    "mongodb": "mongodb",
    "mongodb+motor": "mongodb",
    "redis": "redis",
    "redis+asyncio": "redis",
}

_DEFAULT_PORT = {
    "postgres": 5432,
    "mysql": 3306,
    "mongodb": 27017,
    "redis": 6379,
}


@dataclass(frozen=True)
class BackendURL:
    """A parsed database DSN for a non-protocol backend."""

    scheme: str
    family: str
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None
    options: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    @property
    def address(self) -> str:
        return f"{self.host or 'localhost'}:{self.port or 0}"


def backend_family(dsn: str) -> str:
    """Return the backend family a DSN routes to, or raise for an unknown scheme."""
    scheme = urlsplit(dsn).scheme.lower()
    family = SCHEME_FAMILY.get(scheme)
    if family is None:
        raise AuraError(
            f"Unsupported DSN scheme {scheme!r}; supported schemes: "
            f"{', '.join(sorted(SCHEME_FAMILY))}",
            code="config_error",
        )
    return family


def parse_backend_url(dsn: str) -> BackendURL:
    """Parse a database DSN into a :class:`BackendURL` (no driver import)."""
    parts = urlsplit(dsn)
    scheme = parts.scheme.lower()
    family = backend_family(dsn)
    query = parse_qs(parts.query, keep_blank_values=True)
    options = {key: values[-1] for key, values in query.items()}

    if family == "sqlite":
        # sqlite:///path/to.db  -> database = "/path/to.db"; sqlite:// (no path) -> in-memory.
        database = unquote(parts.path) or None
        if parts.netloc and parts.netloc not in {"", "localhost"}:
            # sqlite://:memory: style or sqlite://relative — keep the netloc as the target.
            database = parts.netloc + (parts.path or "")
        return BackendURL(scheme=scheme, family=family, database=database, options=options, raw=dsn)

    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    database = parts.path.lstrip("/") or None
    return BackendURL(
        scheme=scheme,
        family=family,
        host=parts.hostname,
        port=parts.port or _DEFAULT_PORT.get(family),
        username=username,
        password=password,
        database=database,
        options=options,
        raw=dsn,
    )


def _config_for(url: BackendURL) -> ClientConfig:
    """Build a lightweight :class:`ClientConfig` describing a non-protocol backend."""
    return ClientConfig(
        scheme=url.scheme,
        host=url.host or "localhost",
        port=url.port or 0,
        database=url.database,
        options=dict(url.options),
    )


def resolve_backend(
    dsn: str,
    *,
    metrics: Metrics,
    telemetry: _TelemetryBridge,
    transport: Transport | None = None,
    **options: Any,
) -> tuple[ClientConfig, Backend]:
    """Resolve a DSN to a ``(ClientConfig, Backend)`` pair.

    ``transport`` overrides transport construction for protocol backends (used by tests and
    advanced callers). ``options`` are forwarded to :func:`~aura.config.parse_dsn` for
    protocol schemes.
    """
    family = backend_family(dsn)

    if family in {"auradb", "memory"}:
        from ..transport.memory import MemoryTransport
        from ..transport.tcp import TCPTransport
        from .auradb import AuraDBBackend
        from .memory import MemoryBackend

        config = parse_dsn(dsn, **options)
        if family == "memory":
            transport = transport or MemoryTransport(max_payload_bytes=config.max_payload_bytes)
            return config, MemoryBackend(config, transport, metrics, telemetry)
        transport = transport or TCPTransport(config)
        return config, AuraDBBackend(config, transport, metrics, telemetry)

    url = parse_backend_url(dsn)
    config = _config_for(url)

    if family == "sqlite":
        from .sqlite import SQLiteBackend

        return config, SQLiteBackend(url, metrics)
    if family == "postgres":
        from .postgres import PostgresBackend

        return config, PostgresBackend(url, metrics)
    if family == "mysql":
        from .mysql import MySQLBackend

        return config, MySQLBackend(url, metrics)
    if family == "mongodb":
        from .mongodb import MongoDBBackend

        return config, MongoDBBackend(url, metrics)
    if family == "redis":
        from .redis import RedisBackend

        return config, RedisBackend(url, metrics)

    raise AuraError(  # pragma: no cover - families are exhaustive
        f"No backend registered for family {family!r}", code="config_error"
    )
