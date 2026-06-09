"""The Aura client: async lifecycle, model registry, and query execution.

``Client`` is the public entry point. It owns the transport and a model registry,
implements the :class:`~aura.query.builder.Executor` contract used by query builders,
maps server error frames to typed exceptions, and provides transactions. It performs no
network IO at import time or in ``__init__`` — only ``connect``/``close``/requests do IO.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from types import TracebackType
from typing import Any, TypeVar

from .backends.auradb import _ERROR_CODE_MAP as _ERROR_CODE_MAP  # re-exported for compatibility
from .backends.auradb import ProtocolBackend, error_from_frame
from .backends.base import Backend, BackendResult
from .config import ClientConfig
from .errors import (
    AuraBackendCapabilityError,
    AuraCapabilityError,
    AuraClientClosedError,
    AuraConnectionError,
    AuraNotLeaderError,
    AuraQueryError,
    AuraSchemaError,
    AuraServerError,
    AuraTransactionError,
)
from .hydration.hydrator import Hydrator
from .models import AuraModel, get_model
from .observability import Metrics, TelemetryConfig, _TelemetryBridge
from .protocol.frames import Frame
from .protocol.opcodes import PROTOCOL_VERSION
from .query.ast import InsertQuery, QueryNode, RawQuery, UpsertQuery
from .query.builder import (
    AggregateResult,
    DeleteBuilder,
    Page,
    QueryBuilder,
    QueryResult,
    SearchResultPage,
    TraverseBuilder,
    UpdateBuilder,
)
from .query.expressions import FieldReference
from .transport.base import Transport

__all__ = ["Aura", "Client", "LeaderRedirect", "Transaction", "connect"]

_T = TypeVar("_T")

_MUTATION_OPS = frozenset({"insert", "update", "delete", "upsert"})

#: Hard ceiling on leader redirects per operation, so an opt-in redirect helper can
#: never become an unbounded retry loop even if misconfigured.
_MAX_LEADER_REDIRECTS = 10

#: Canonical transaction isolation token. AuraDB provides **snapshot isolation with
#: optimistic (first-committer-wins) conflict detection** on commit; it does not
#: provide serializable isolation, and the connector does not upgrade it. New code
#: should pass ``"snapshot"`` (``"snapshot_isolation"`` is accepted as a synonym).
DEFAULT_ISOLATION = "snapshot"

#: Accepted aliases that normalize to :data:`DEFAULT_ISOLATION`. ``"serializable"`` is a
#: deprecated compatibility alias kept so existing callers do not break: it maps to
#: AuraDB snapshot isolation and does **not** change AuraDB transaction semantics.
_ISOLATION_ALIASES = {
    "snapshot": "snapshot",
    "snapshot_isolation": "snapshot",
    "serializable": "snapshot",  # deprecated alias — AuraDB is not serializable
}


def _normalize_isolation(isolation: str) -> str:
    """Map an isolation token to its canonical form.

    Known tokens (``"snapshot"``, ``"snapshot_isolation"``, and the deprecated
    ``"serializable"`` alias) normalize to ``"snapshot"`` so the connector never sends
    or claims serializable isolation against AuraDB. Unrecognized tokens pass through
    unchanged — AuraDB ignores the wire token and always applies snapshot isolation,
    and database backends manage isolation through their own driver.
    """
    return _ISOLATION_ALIASES.get(isolation, isolation)


#: DSN schemes the redirect helpers accept in an explicit leader address. These
#: mirror the wire-protocol schemes :mod:`aura.config` understands; a redirect
#: target that names any other scheme is rejected rather than silently parsed.
_SECURE_REDIRECT_SCHEMES = frozenset({"auras", "auradbs"})
_INSECURE_REDIRECT_SCHEMES = frozenset({"aura", "aura+tcp", "auradb", "aura+memory", "memory"})
_KNOWN_REDIRECT_SCHEMES = _SECURE_REDIRECT_SCHEMES | _INSECURE_REDIRECT_SCHEMES


def _split_address(address: str) -> tuple[str, int, str | None]:
    """Parse a leader address into ``(host, port, scheme)``.

    Accepts a bare ``host:port`` as well as a full ``auradb://host:port`` URL. The
    returned ``scheme`` is the explicit scheme when the address carried one (so a
    caller can enforce a secure-redirect policy) and ``None`` for a bare address.
    Raises a clear :class:`AuraConnectionError` for a missing, malformed, or
    unknown-scheme address so a caller redirecting to a leader gets an actionable
    failure rather than a generic parse error or a silently mis-parsed target.
    """
    if not isinstance(address, str) or not address.strip():
        raise AuraConnectionError("a concrete leader address is required to reconnect")
    text = address.strip()
    scheme: str | None = None
    if "://" in text:
        from urllib.parse import urlsplit

        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower() or None
        if scheme is not None and scheme not in _KNOWN_REDIRECT_SCHEMES:
            raise AuraConnectionError(
                f"leader address {address!r} uses unsupported scheme {scheme!r}; "
                f"expected one of {sorted(_KNOWN_REDIRECT_SCHEMES)} or a bare host:port"
            )
        host, port = parts.hostname, parts.port
    elif text.startswith("[") and "]" in text:  # bracketed IPv6, optional :port
        host_part, _, port_part = text.rpartition("]")
        host = host_part.lstrip("[")
        port = int(port_part.lstrip(":")) if port_part.startswith(":") else None
    else:
        host, _, port_text = text.rpartition(":")
        if not host:  # no colon present
            raise AuraConnectionError(f"leader address {address!r} must be in 'host:port' form")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise AuraConnectionError(f"leader address {address!r} has a non-numeric port") from exc
    if not host or port is None:
        raise AuraConnectionError(f"leader address {address!r} must be in 'host:port' form")
    return host, int(port), scheme


def _field_name(key: Any) -> str:
    if isinstance(key, FieldReference):
        return key.name
    return str(key)


def _serialize_assignments(values: dict[Any, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        out[_field_name(key)] = _serialize_value(value)
    return out


def _serialize_value(value: Any) -> Any:
    if isinstance(value, AuraModel):
        return value.primary_key_value()
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


class _BoundExecutor:
    """Adapts the client's execution to a specific transaction id for builders."""

    __slots__ = ("_client", "_txid")

    def __init__(self, client: Client, txid: int) -> None:
        self._client = client
        self._txid = txid

    async def run(self, node: QueryNode, model: type[AuraModel]) -> QueryResult:
        return await self._client._execute(node, model, self._txid)

    def stream(
        self, node: QueryNode, model: type[AuraModel], batch_size: int
    ) -> AsyncIterator[Any]:
        return self._client._stream(node, model, batch_size, self._txid)

    def search_pages(
        self, node: QueryNode, model: type[AuraModel], page_size: int
    ) -> AsyncIterator[Any]:
        return self._client._search_pages(node, model, page_size, self._txid)

    async def aggregate(self, node: QueryNode, model: type[AuraModel]) -> AggregateResult:
        return await self._client._aggregate(node, model, self._txid)

    async def resume_page(
        self, node: QueryNode, model: type[AuraModel], page_size: int, cursor: str | None
    ) -> Page[Any]:
        return await self._client._resume_page(node, model, page_size, cursor, self._txid)


class Client:
    """Async client for an Aura/AuraDB server."""

    def __init__(
        self,
        config: ClientConfig,
        transport: Transport | None = None,
        models: Iterable[type[AuraModel]] = (),
        *,
        telemetry: Any = None,
        backend: Backend | None = None,
        metrics: Metrics | None = None,
        telemetry_bridge: _TelemetryBridge | None = None,
    ) -> None:
        self._config = config
        self._metrics = metrics if metrics is not None else Metrics()
        self._telemetry = (
            telemetry_bridge
            if telemetry_bridge is not None
            else _TelemetryBridge(TelemetryConfig.from_value(telemetry))
        )
        if backend is None:
            if transport is None:
                raise AuraConnectionError("Client requires either a transport or a backend")
            backend = ProtocolBackend(config, transport, self._metrics, self._telemetry)
        self._backend = backend
        self._registry: dict[str, type[AuraModel]] = {}
        self._hydrator = Hydrator()
        self._closed = False
        self._opened = False
        self._tx_counter = 0
        self._root_executor = _BoundExecutor(self, 0)
        for model in models:
            self.register_model(model)

    # -- construction / lifecycle ------------------------------------------------
    @classmethod
    def connect(
        cls,
        dsn: str,
        *,
        models: Iterable[type[AuraModel]] = (),
        transport: Transport | None = None,
        **options: Any,
    ) -> _ClientConnector:
        """Return a connector that is both awaitable and an async context manager."""
        telemetry = options.pop("telemetry", None)
        return _ClientConnector(cls, dsn, tuple(models), transport, telemetry, options)

    @property
    def config(self) -> ClientConfig:
        return self._config

    @property
    def backend(self) -> Backend:
        """The storage backend this client executes against."""
        return self._backend

    @property
    def transport(self) -> Transport:
        """The underlying transport for protocol backends.

        Available only when this client runs over the Aura Wire Protocol (the AuraDB and
        memory backends). Other backends speak to a database driver and expose no transport.
        """
        transport: Transport | None = getattr(self._backend, "transport", None)
        if transport is None:
            raise AuraBackendCapabilityError(
                f"Backend {self._backend.name!r} does not expose a wire transport",
                context={"backend": self._backend.name},
            )
        return transport

    @property
    def metrics(self) -> Metrics:
        """In-process observability metrics for this client."""
        return self._metrics

    def capabilities(self) -> Any:
        """Return the backend's :class:`~aura.backends.capabilities.BackendCapabilities`."""
        return self._backend.capabilities()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def register_model(self, model: type[AuraModel]) -> None:
        """Register a model (and its relationship targets) with the client."""
        if model.__name__ in self._registry:
            return
        self._registry[model.__name__] = model
        for rel in model.__aura_schema__.relationships:
            target = get_model(rel.target)
            if target is not None and target.__name__ not in self._registry:
                self.register_model(target)

    def model(self, name: str) -> type[AuraModel]:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise AuraSchemaError(
                f"Model {name!r} is not registered with this client",
                context={"model": name},
            ) from exc

    async def _open(self) -> None:
        await self._backend.connect()
        if self._registry:
            await self._backend.create_schema(self._registry.values())
        self._opened = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._backend.close()

    async def __aenter__(self) -> Client:
        if not self._opened:
            await self._open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- health ------------------------------------------------------------------
    async def ping(self) -> bool:
        """Round-trip a ping; returns ``True`` when the backend is reachable."""
        self._ensure_open()
        return await self._backend.ping()

    async def health(self) -> dict[str, Any]:
        """Return a non-sensitive health/diagnostic snapshot."""
        ok = await self.ping()
        return {
            "status": "ok" if ok else "degraded",
            "protocol_version": PROTOCOL_VERSION,
            "address": self._config.address,
            "backend": self._backend.name,
            "capabilities": self._backend.capabilities().to_dict(),
            "transport": self._backend.describe(),
            "stats": self._backend.stats(),
            "metrics": self._metrics.snapshot(),
            "models": sorted(self._registry),
        }

    async def server_schema(self) -> list[dict[str, Any]]:
        """Fetch the schema the backend currently knows about (protocol backends only)."""
        self._ensure_open()
        fetch = getattr(self._backend, "server_schema", None)
        if fetch is None:
            raise AuraBackendCapabilityError(
                f"Backend {self._backend.name!r} does not expose a server schema",
                context={"backend": self._backend.name},
            )
        result: list[dict[str, Any]] = await fetch()
        return result

    # -- query entry points ------------------------------------------------------
    def query(self, model: type[AuraModel]) -> QueryBuilder:
        self.register_model(model)
        return QueryBuilder(model, self._root_executor)

    def search(self, model: type[AuraModel]) -> QueryBuilder:
        """Alias of :meth:`query` for vector/hybrid search readability."""
        return self.query(model)

    async def resume_search(
        self, search: QueryBuilder, cursor: str, *, page_size: int = 50
    ) -> Page[Any]:
        """Resume a ranked search from an externally-held opaque ``cursor`` token.

        ``search`` is the same ranked builder (``search_text``/``search_vector``
        including the approximate preview/``search_hybrid``) that produced the
        token; ``cursor`` is a ``next_cursor`` from an earlier :class:`Page` (for
        example one persisted across processes). Returns the next :class:`Page`.

        The token is opaque and forwarded verbatim — never parse it — and its
        lifetime is bounded by the server, so resume promptly. For BM25 and hybrid
        results that must stay stable under concurrent writes, run the original
        search and the resume inside the same snapshot transaction. Raises
        :class:`~aura.AuraCapabilityError` on a backend without ``cursor_resume``.
        """
        self.register_model(search.model)
        if not isinstance(cursor, str) or not cursor:
            raise AuraQueryError("resume_search requires a non-empty opaque cursor token")
        return await self._resume_page(search.query, search.model, page_size, cursor, 0)

    def traverse(self, model: type[AuraModel]) -> TraverseBuilder:
        self.register_model(model)
        return TraverseBuilder(model, self._root_executor)

    def update(self, model: type[AuraModel]) -> UpdateBuilder:
        self.register_model(model)
        return UpdateBuilder(model, self._root_executor)

    def delete(self, model: type[AuraModel]) -> DeleteBuilder:
        self.register_model(model)
        return DeleteBuilder(model, self._root_executor)

    async def insert(self, instance: AuraModel, *, on_conflict: str | None = None) -> AuraModel:
        return await self._insert_obj(instance, on_conflict, 0)

    async def bulk_insert(
        self,
        model: type[AuraModel],
        rows: Sequence[AuraModel | dict[str, Any]],
        *,
        batch_size: int = 1000,
        on_conflict: str | None = None,
    ) -> int:
        return await self._bulk_insert(model, rows, batch_size, on_conflict, 0)

    async def upsert(
        self,
        model: type[AuraModel],
        *,
        key: dict[Any, Any],
        values: dict[Any, Any],
    ) -> AuraModel:
        return await self._upsert_obj(model, key, values, 0)

    async def raw(
        self, statement: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a bounded raw statement with bound parameters."""
        self._ensure_open()
        node = RawQuery(statement=statement, params=dict(params or {}))
        result = await self._execute(node, AuraModel, 0)
        return [dict(r) for r in result.rows]

    def transaction(self, *, isolation: str = DEFAULT_ISOLATION) -> Transaction:
        """Open a transaction.

        AuraDB transactions provide snapshot isolation with optimistic conflict
        detection on commit (first-committer-wins); the connector does not upgrade
        them to serializable isolation. ``isolation`` defaults to ``"snapshot"``. The
        legacy ``"serializable"`` token is accepted as a deprecated compatibility
        alias for snapshot isolation and should not be used in new code.
        """
        self._ensure_open()
        self._tx_counter += 1
        return Transaction(self, self._tx_counter, _normalize_isolation(isolation))

    # -- cluster leader redirection (AuraDB multi-node preview) ------------------
    def _require_protocol_backend(self) -> None:
        """Reject leader redirection for backends that have no wire transport.

        AuraDB's cluster preview only applies to the Aura Wire Protocol backends.
        Database adapters (SQLite, PostgreSQL, …) have a single endpoint and no
        notion of a leader, so redirect helpers refuse rather than silently no-op.
        """
        from .backends.registry import _PROTOCOL_FAMILIES, SCHEME_FAMILY

        if SCHEME_FAMILY.get(self._config.scheme) not in _PROTOCOL_FAMILIES:
            raise AuraBackendCapabilityError(
                f"Backend {self._backend.name!r} (scheme {self._config.scheme!r}) is not an "
                "AuraDB wire-protocol backend and has no cluster leader to redirect to",
                context={"backend": self._backend.name, "scheme": self._config.scheme},
            )

    @staticmethod
    def _leader_address_from(target: AuraNotLeaderError | str) -> str:
        if isinstance(target, AuraNotLeaderError):
            address = target.leader_addr
            if not address:
                raise AuraConnectionError(
                    "not_leader response did not include a usable leader address; "
                    "resolve the leader (e.g. `auradb cluster leader`) and reconnect explicitly",
                    context={"code": target.code},
                )
            return address
        return target

    def _resolve_redirect_target(self, address: str, *, allow_insecure: bool) -> tuple[str, int]:
        """Validate a redirect ``address`` against this client's security posture.

        Returns the ``(host, port)`` to redirect to. The new connection always
        inherits this client's scheme, TLS, and auth (only host/port change), so a
        secure client stays secure. The one thing we refuse is an *explicit*
        downgrade: if this client is on a TLS scheme and the address names a
        plaintext scheme (e.g. ``auradb://``), redirecting would be a silent
        security downgrade, so we fail closed unless ``allow_insecure`` is set.
        """
        host, port, scheme = _split_address(address)
        current_secure = self._config.scheme in _SECURE_REDIRECT_SCHEMES or self._config.tls.enabled
        if (
            not allow_insecure
            and current_secure
            and scheme is not None
            and scheme in _INSECURE_REDIRECT_SCHEMES
        ):
            raise AuraConnectionError(
                f"refusing to redirect a secure client (scheme {self._config.scheme!r}, "
                f"TLS enabled) to insecure leader address {address!r}; this would silently "
                "drop TLS. Pass a secure or bare host:port address, or set allow_insecure=True "
                "to override deliberately.",
                context={"from_scheme": self._config.scheme, "to_scheme": scheme},
            )
        return host, port

    async def connect_to_leader(
        self,
        error: AuraNotLeaderError,
        *,
        models: Iterable[type[AuraModel]] | None = None,
        allow_insecure: bool = False,
    ) -> Client:
        """Open a **new** client connected to the leader named by ``error``.

        Catch :class:`~aura.AuraNotLeaderError` from a write against a follower and
        call this to obtain a fresh client bound to the current leader. The new
        client inherits this client's scheme, authentication, and TLS configuration
        unchanged — a token stays a token, certificate verification stays on — only
        the host and port change. This client is left untouched and usable; the new
        client carries no transaction state from it.

        Raises :class:`~aura.AuraConnectionError` when ``error`` carries no usable
        leader address or when redirecting would silently drop TLS (pass
        ``allow_insecure=True`` to override), and
        :class:`~aura.AuraBackendCapabilityError` for non-AuraDB backends.
        """
        return await self.reconnect_to(
            self._leader_address_from(error), models=models, allow_insecure=allow_insecure
        )

    async def reconnect_to(
        self,
        address: str,
        *,
        models: Iterable[type[AuraModel]] | None = None,
        allow_insecure: bool = False,
    ) -> Client:
        """Open a new client to ``address`` (``host:port``), preserving auth/TLS.

        A concrete address is required; an empty, malformed, or unknown-scheme
        address raises :class:`~aura.AuraConnectionError`, as does an explicit
        insecure address when this client is secure (unless ``allow_insecure=True``).
        The returned client is independent of this one (separate connection, no
        shared transaction state) and registers the same models by default so it is
        immediately usable.
        """
        self._require_protocol_backend()
        host, port = self._resolve_redirect_target(address, allow_insecure=allow_insecure)
        new_config = self._config.with_overrides(host=host, port=port)
        registered = tuple(self._registry.values()) if models is None else tuple(models)
        return await self._open_sibling(new_config, registered)

    async def _open_sibling(
        self, config: ClientConfig, models: tuple[type[AuraModel], ...]
    ) -> Client:
        from .backends.registry import backend_from_config

        metrics = Metrics()
        telemetry = _TelemetryBridge(TelemetryConfig.from_value(None))
        backend = backend_from_config(config, metrics=metrics, telemetry=telemetry)
        client = type(self)(
            config,
            backend=backend,
            models=models,
            metrics=metrics,
            telemetry_bridge=telemetry,
        )
        await client._open()
        return client

    async def _reconnect_backend_to(self, address: str) -> None:
        """Re-point **this** client's backend at ``address`` in place, preserving config.

        Used by :meth:`with_leader_redirect`. The old backend is closed and a new
        one is opened against the leader with the same authentication and TLS
        settings. Registered models are re-declared so the client stays usable.
        Transactions are not migrated; callers must not use this mid-transaction.
        A redirect that would silently drop TLS is refused (see
        :meth:`_resolve_redirect_target`); the leader address from a ``not_leader``
        response is a bare ``host:port`` and inherits this client's TLS unchanged.
        """
        self._require_protocol_backend()
        host, port = self._resolve_redirect_target(address, allow_insecure=False)
        new_config = self._config.with_overrides(host=host, port=port)
        new_backend = self._build_backend(new_config)
        old_backend = self._backend
        self._backend = new_backend
        self._config = new_config
        try:
            await new_backend.connect()
            if self._registry:
                await new_backend.create_schema(self._registry.values())
        finally:
            await old_backend.close()

    def _build_backend(self, config: ClientConfig) -> Backend:
        """Construct a protocol backend for ``config`` (override point for tests)."""
        from .backends.registry import backend_from_config

        return backend_from_config(config, metrics=self._metrics, telemetry=self._telemetry)

    def with_leader_redirect(self, *, max_redirects: int = 1) -> LeaderRedirect:
        """Return an **opt-in** wrapper that redirects writes to the cluster leader.

        Disabled by default: plain client calls never redirect. The wrapper retries
        an operation against the current leader **only** when the server returns a
        ``not_leader`` response that carries a usable leader address, and never more
        than ``max_redirects`` times — there is no unbounded retry. ::

            leader = client.with_leader_redirect(max_redirects=1)
            await leader.insert(Widget(id=1, name="a"))

        This is safe specifically because ``not_leader`` is a *pre-application*
        rejection: a follower refuses a write before it enters the Raft log, so
        retrying the same write on the leader cannot double-apply it. The wrapper
        only ever reacts to ``not_leader`` — never to ambiguous network or timeout
        errors, where a write may or may not have landed.

        Transactions and streaming cursors are **not** redirected: their server-side
        state lives on the original node and cannot migrate. Use the wrapper for
        autonomous writes; restart a transaction or a stream against the leader
        yourself (see :meth:`connect_to_leader`).
        """
        self._ensure_open()
        self._require_protocol_backend()
        return LeaderRedirect(self, max_redirects)

    # -- dynamic model access ----------------------------------------------------
    def __getattr__(self, name: str) -> QueryBuilder:
        # Only called for attributes not found normally; resolve registered models.
        registry = self.__dict__.get("_registry", {})
        if name in registry:
            return QueryBuilder(registry[name], self.__dict__["_root_executor"])
        raise AttributeError(name)

    # -- Executor implementation -------------------------------------------------
    def _check_search_capabilities(self, ir: dict[str, Any]) -> None:
        """Fail clearly when a query uses a search feature the backend lacks,
        rather than silently dropping the clause or emulating it."""
        caps = self._backend.capabilities()
        required: list[tuple[str, str]] = []
        if "hybrid" in ir:
            required.append(("hybrid_search", "hybrid search"))
        if "text_search" in ir:
            required.append(("full_text_search", "ranked full-text search"))
        if "vector" in ir:
            required.append(("vector_search", "vector search"))
        for flag, label in required:
            if not caps.supports(flag):
                raise AuraBackendCapabilityError(
                    f"Backend {caps.name!r} does not support {label}",
                    context={"backend": caps.name, "capability": flag},
                )

    async def _execute(self, node: QueryNode, model: type[AuraModel], txid: int) -> QueryResult:
        self._ensure_open()
        ir = node.to_ir()
        if node.operation == "select":
            self._check_search_capabilities(ir)
        self._metrics.record_query(node.operation)
        if node.operation in _MUTATION_OPS:
            result = await self._backend.execute_mutation(ir, txid=txid)
        else:
            result = await self._backend.execute_query(ir, txid=txid)

        deserialize_start = time.perf_counter()
        interpreted = self._interpret(node, model, result)
        self._metrics.deserialize_latency.record(time.perf_counter() - deserialize_start)
        return interpreted

    def _interpret(
        self, node: QueryNode, model: type[AuraModel], result: BackendResult
    ) -> QueryResult:
        operation = node.operation

        if operation in _MUTATION_OPS:
            rows = self._hydrator.hydrate_rows(model, result.rows) if result.rows else []
            return QueryResult(rows=rows, affected=result.affected, request_id=result.request_id)
        if operation in {"count", "exists"}:
            return QueryResult(count=result.count, request_id=result.request_id)
        if operation == "raw":
            return QueryResult(rows=list(result.rows), request_id=result.request_id)
        if operation == "traverse":
            target_name = result.metadata.get("model", model.__name__)
            target = self._registry.get(target_name, model)
            rows = self._hydrator.hydrate_rows(target, result.rows)
            return QueryResult(rows=rows, request_id=result.request_id)

        partial = bool(getattr(node, "projection", ()))
        rows = self._hydrator.hydrate_rows(model, result.rows, partial=partial)
        return QueryResult(
            rows=rows,
            count=result.count,
            request_id=result.request_id,
            metadata=dict(result.metadata),
        )

    async def _stream(
        self, node: QueryNode, model: type[AuraModel], batch_size: int, txid: int
    ) -> AsyncIterator[Any]:
        """Yield hydrated rows page-by-page over a backend cursor.

        Iteration is bounded: the backend pulls one ``batch_size`` page at a time, holding
        at most one page in memory regardless of result size. If the consumer stops early
        (``break``/``aclose``), the backend releases the open cursor and the cancellation is
        counted; the client stays fully usable afterwards.
        """
        self._ensure_open()
        if batch_size <= 0:
            raise AuraQueryError("batch_size must be positive")

        self._metrics.record_query(node.operation)
        pages = self._backend.stream_query(node.to_ir(), batch_size, txid=txid)
        cancelled = False
        try:
            async for raw in pages:
                yield self._hydrator.hydrate_row(model, raw)
        except GeneratorExit:
            cancelled = True
            raise
        finally:
            aclose = getattr(pages, "aclose", None)
            if aclose is not None:
                await aclose()
            if cancelled:
                self._metrics.record_stream_cancellation()

    async def _search_pages(
        self, node: QueryNode, model: type[AuraModel], page_size: int, txid: int
    ) -> AsyncIterator[SearchResultPage]:
        """Drive ranked pagination: issue a ``search_page`` read per page, hydrate
        the rows, and follow the server's ``next_cursor`` until exhausted. Bounds
        the page size; the backend rejects an unsupported backend or a malformed
        cursor with a structured error, leaving the client usable."""
        self._ensure_open()
        if page_size <= 0:
            raise AuraQueryError("page_size must be positive")
        base_ir = node.to_ir()
        if not any(k in base_ir for k in ("vector", "text_search", "hybrid")):
            raise AuraQueryError(
                "search_pages requires a ranked clause "
                "(search_text, search_vector, or search_hybrid)"
            )
        cursor: str | None = None
        while True:
            page_ir = {**base_ir, "operation": "search_page", "page_size": page_size}
            if cursor is not None:
                page_ir["cursor"] = cursor
            self._metrics.record_query("search_page")
            result = await self._backend.execute_query(page_ir, txid=txid)
            rows = self._hydrator.hydrate_rows(model, result.rows) if result.rows else []
            next_cursor = result.metadata.get("next_cursor")
            yield SearchResultPage(rows=rows, cursor=next_cursor)
            if not next_cursor:
                break
            cursor = next_cursor

    async def _aggregate(
        self, node: QueryNode, model: type[AuraModel], txid: int
    ) -> AggregateResult:
        """Issue an ``aggregate`` read (count/min/max metrics and terms facets) and
        parse the structured result. The backend rejects an unsupported backend
        with a clear capability error, leaving the client usable."""
        self._ensure_open()
        base_ir = node.to_ir()
        facets = list(getattr(node, "facets", ()))
        metrics = list(getattr(node, "metrics", ()))
        group_by = getattr(node, "group_by", None)
        profile = bool(getattr(node, "profile", False))
        if not facets and not metrics and group_by is None:
            raise AuraQueryError("aggregate() requires at least one facet, metric, or group_by")
        if group_by is not None:
            self._require_capability("group_by", "group-by aggregation")
        if profile:
            self._require_capability("query_profile", "query profiling")
        agg_ir: dict[str, Any] = {
            **base_ir,
            "operation": "aggregate",
            "facets": facets,
            "metrics": metrics,
        }
        if group_by is not None:
            agg_ir["group_by"] = group_by.to_ir()
        if profile:
            agg_ir["profile"] = True
        self._metrics.record_query("aggregate")
        result = await self._backend.execute_query(agg_ir, txid=txid)
        body = result.metadata.get("aggregate")
        if body is None:
            raise AuraQueryError("aggregate response did not include a result")
        return AggregateResult.from_dict(body)

    def _require_capability(self, flag: str, label: str) -> None:
        """Fail clearly when the backend lacks an explicitly requested capability.

        Unknown flags (a backend whose capability matrix predates this connector)
        are treated as unsupported rather than crashing, so the caller gets a clean
        :class:`AuraCapabilityError` instead of a ``ValueError``.
        """
        caps = self._backend.capabilities()
        try:
            supported = caps.supports(flag)
        except ValueError:
            supported = False
        if not supported:
            raise AuraCapabilityError(
                f"Backend {caps.name!r} does not support {label}",
                context={"backend": caps.name, "capability": flag},
            )

    async def _resume_page(
        self,
        node: QueryNode,
        model: type[AuraModel],
        page_size: int,
        cursor: str | None,
        txid: int,
    ) -> Page[Any]:
        """Fetch one ranked-search page, optionally resuming from an opaque ``cursor``.

        Issues a single ``search_page`` read and returns a public :class:`Page`. The
        cursor token stays opaque (it is forwarded verbatim and never parsed). The
        backend rejects an unsupported backend or a malformed/expired cursor with a
        structured error, leaving the client usable.
        """
        self._ensure_open()
        if page_size <= 0:
            raise AuraQueryError("page_size must be positive")
        self._require_capability("cursor_resume", "ranked-search cursor resume")
        base_ir = node.to_ir()
        if not any(k in base_ir for k in ("vector", "text_search", "hybrid")):
            raise AuraQueryError(
                "resume_search/page requires a ranked clause "
                "(search_text, search_vector, or search_hybrid)"
            )
        page_ir: dict[str, Any] = {**base_ir, "operation": "search_page", "page_size": page_size}
        if cursor is not None:
            page_ir["cursor"] = cursor
        self._metrics.record_query("search_page")
        result = await self._backend.execute_query(page_ir, txid=txid)
        rows = self._hydrator.hydrate_rows(model, result.rows) if result.rows else []
        next_cursor = result.metadata.get("next_cursor")
        total = result.metadata.get("total")
        return Page(
            items=rows,
            next_cursor=str(next_cursor) if next_cursor else None,
            total=int(total)
            if isinstance(total, (int, float)) and not isinstance(total, bool)
            else None,
        )

    # -- mutation helpers (shared by client and transactions) --------------------
    async def _insert_obj(
        self, instance: AuraModel, on_conflict: str | None, txid: int
    ) -> AuraModel:
        model = type(instance)
        self.register_model(model)
        node = InsertQuery(
            model=model.__name__, rows=(instance.to_dict(),), on_conflict=on_conflict
        )
        result = await self._execute(node, model, txid)
        return result.rows[0] if result.rows else instance

    async def _bulk_insert(
        self,
        model: type[AuraModel],
        rows: Sequence[AuraModel | dict[str, Any]],
        batch_size: int,
        on_conflict: str | None,
        txid: int,
    ) -> int:
        if batch_size <= 0:
            raise AuraQueryError("batch_size must be positive")
        self.register_model(model)
        payload_rows = [r.to_dict() if isinstance(r, AuraModel) else dict(r) for r in rows]
        affected = 0
        for start in range(0, len(payload_rows), batch_size):
            chunk = payload_rows[start : start + batch_size]
            node = InsertQuery(model=model.__name__, rows=tuple(chunk), on_conflict=on_conflict)
            result = await self._execute(node, model, txid)
            affected += result.affected or 0
        return affected

    async def _upsert_obj(
        self,
        model: type[AuraModel],
        key: dict[Any, Any],
        values: dict[Any, Any],
        txid: int,
    ) -> AuraModel:
        self.register_model(model)
        node = UpsertQuery(
            model=model.__name__,
            key={_field_name(k): _serialize_value(v) for k, v in key.items()},
            values=_serialize_assignments(values),
        )
        result = await self._execute(node, model, txid)
        if not result.rows:
            raise AuraServerError("Upsert returned no row")
        row: AuraModel = result.rows[0]
        return row

    # -- transaction control -----------------------------------------------------
    async def _tx_control(self, action: str, txid: int, isolation: str) -> None:
        if action == "begin":
            await self._backend.begin(txid, isolation)
        elif action == "commit":
            await self._backend.commit(txid, isolation)
        elif action == "rollback":
            await self._backend.rollback(txid, isolation)
        else:  # pragma: no cover - guarded by callers
            raise AuraQueryError(f"Unknown transaction action {action!r}")

    def _raise_error(self, frame: Frame) -> None:
        """Map a protocol ``ERROR`` frame to a typed exception and record it.

        Retained as a stable entry point; the canonical error map lives in the AuraDB
        backend (:data:`aura.backends.auradb._ERROR_CODE_MAP`).
        """
        error = error_from_frame(frame)
        self._metrics.record_error(error.code)
        raise error

    def _ensure_open(self) -> None:
        if self._closed:
            raise AuraClientClosedError("Client is closed")
        if not self._opened:
            raise AuraConnectionError("Client is not connected; use 'async with' or await connect")


class Transaction:
    """An async transaction context manager.

    Mutations and queries issued through the transaction run under its transaction id;
    the reference server stages them on an overlay that is committed on clean exit and
    discarded on error. AuraDB runs the transaction under snapshot isolation with
    optimistic conflict detection on commit (first-committer-wins); it is not
    serializable, and the connector does not upgrade it.
    """

    def __init__(self, client: Client, txid: int, isolation: str) -> None:
        self._client = client
        self._txid = txid
        self._isolation = isolation
        self._executor = _BoundExecutor(client, txid)
        self._finished = False

    @property
    def id(self) -> int:
        return self._txid

    def query(self, model: type[AuraModel]) -> QueryBuilder:
        self._client.register_model(model)
        return QueryBuilder(model, self._executor)

    def search(self, model: type[AuraModel]) -> QueryBuilder:
        return self.query(model)

    def update(self, model: type[AuraModel]) -> UpdateBuilder:
        self._client.register_model(model)
        return UpdateBuilder(model, self._executor)

    def delete(self, model: type[AuraModel]) -> DeleteBuilder:
        self._client.register_model(model)
        return DeleteBuilder(model, self._executor)

    async def insert(self, instance: AuraModel, *, on_conflict: str | None = None) -> AuraModel:
        return await self._client._insert_obj(instance, on_conflict, self._txid)

    async def upsert(
        self, model: type[AuraModel], *, key: dict[Any, Any], values: dict[Any, Any]
    ) -> AuraModel:
        return await self._client._upsert_obj(model, key, values, self._txid)

    async def begin(self) -> None:
        await self._client._tx_control("begin", self._txid, self._isolation)

    async def commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._client._tx_control("commit", self._txid, self._isolation)

    async def rollback(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._client._tx_control("rollback", self._txid, self._isolation)

    async def __aenter__(self) -> Transaction:
        await self.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            await self.rollback()
        else:
            await self.commit()


class LeaderRedirect:
    """Opt-in, bounded leader-redirect wrapper around a :class:`Client`.

    Returned by :meth:`Client.with_leader_redirect`. It exposes the client's
    autonomous write entry points; each call runs against the current node and, if
    the server responds ``not_leader`` with a usable leader address, re-points the
    client at the leader and retries — at most ``max_redirects`` times. Reads run
    through :meth:`run` the same way. Transactions and streaming are intentionally
    not offered here, because their server-side state cannot follow a redirect.
    """

    __slots__ = ("_client", "_max_redirects")

    def __init__(self, client: Client, max_redirects: int) -> None:
        if not isinstance(max_redirects, int) or isinstance(max_redirects, bool):
            raise AuraQueryError("max_redirects must be an integer")
        if max_redirects < 0:
            raise AuraQueryError("max_redirects must be >= 0")
        if max_redirects > _MAX_LEADER_REDIRECTS:
            raise AuraQueryError(
                f"max_redirects must be <= {_MAX_LEADER_REDIRECTS} (bounded redirects only)"
            )
        self._client = client
        self._max_redirects = max_redirects

    @property
    def max_redirects(self) -> int:
        return self._max_redirects

    @property
    def client(self) -> Client:
        return self._client

    async def run(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        """Run a zero-argument coroutine factory, redirecting on ``not_leader``.

        ``operation`` must be re-invocable (a ``lambda`` or ``functools.partial``
        producing a fresh awaitable each call), because a redirect re-runs it on the
        leader. Only ``not_leader`` responses trigger a redirect, and only while a
        usable leader address is supplied and the bounded budget remains; every
        other error propagates unchanged.
        """
        redirects_left = self._max_redirects
        while True:
            try:
                return await operation()
            except AuraNotLeaderError as exc:
                address = exc.leader_addr
                if address is None or redirects_left <= 0:
                    raise
                redirects_left -= 1
                self._client.metrics.record_error("not_leader_redirect")
                await self._client._reconnect_backend_to(address)

    async def insert(self, instance: AuraModel, *, on_conflict: str | None = None) -> AuraModel:
        return await self.run(lambda: self._client.insert(instance, on_conflict=on_conflict))

    async def bulk_insert(
        self,
        model: type[AuraModel],
        rows: Sequence[AuraModel | dict[str, Any]],
        *,
        batch_size: int = 1000,
        on_conflict: str | None = None,
    ) -> int:
        return await self.run(
            lambda: self._client.bulk_insert(
                model, rows, batch_size=batch_size, on_conflict=on_conflict
            )
        )

    async def upsert(
        self, model: type[AuraModel], *, key: dict[Any, Any], values: dict[Any, Any]
    ) -> AuraModel:
        return await self.run(lambda: self._client.upsert(model, key=key, values=values))

    async def raw(
        self, statement: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return await self.run(lambda: self._client.raw(statement, params))

    def transaction(self, *, isolation: str = DEFAULT_ISOLATION) -> Transaction:
        raise AuraTransactionError(
            "leader redirect cannot wrap a transaction: a transaction's server-side state "
            "lives on one node and cannot migrate. On not_leader, restart the transaction on "
            "the leader (see Client.connect_to_leader)."
        )

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise AuraBackendCapabilityError(
            "leader redirect cannot wrap a streaming cursor: an open cursor's state lives on "
            "one node and cannot be redirected mid-stream. Resolve the leader first (catch "
            "AuraNotLeaderError or use Client.connect_to_leader), then start a new query/stream "
            "against the leader."
        )


class _ClientConnector:
    """Awaitable + async-context-manager wrapper returned by :meth:`Client.connect`."""

    __slots__ = ("_client", "_cls", "_dsn", "_models", "_options", "_telemetry", "_transport")

    def __init__(
        self,
        cls: type[Client],
        dsn: str,
        models: tuple[type[AuraModel], ...],
        transport: Transport | None,
        telemetry: Any = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._cls = cls
        self._dsn = dsn
        self._models = models
        self._transport = transport
        self._telemetry = telemetry
        self._options = options or {}
        self._client: Client | None = None

    async def _create(self) -> Client:
        from .backends.registry import resolve_backend

        metrics = Metrics()
        telemetry = _TelemetryBridge(TelemetryConfig.from_value(self._telemetry))
        config, backend = resolve_backend(
            self._dsn,
            metrics=metrics,
            telemetry=telemetry,
            transport=self._transport,
            **self._options,
        )
        client = self._cls(
            config,
            backend=backend,
            models=self._models,
            metrics=metrics,
            telemetry_bridge=telemetry,
        )
        await client._open()
        return client

    def __await__(self) -> Any:
        return self._create().__await__()

    async def __aenter__(self) -> Client:
        self._client = await self._create()
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.close()


class Aura:
    """Top-level facade exposing the ``Aura.connect(...)`` entry point."""

    @staticmethod
    def connect(
        dsn: str,
        *,
        models: Iterable[type[AuraModel]] = (),
        transport: Transport | None = None,
        **options: Any,
    ) -> _ClientConnector:
        return Client.connect(dsn, models=models, transport=transport, **options)


def connect(
    dsn: str,
    *,
    models: Iterable[type[AuraModel]] = (),
    transport: Transport | None = None,
    **options: Any,
) -> _ClientConnector:
    """Module-level convenience for :meth:`Client.connect`."""
    return Client.connect(dsn, models=models, transport=transport, **options)
