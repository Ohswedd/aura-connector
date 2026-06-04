"""The Aura client: async lifecycle, model registry, and query execution.

``Client`` is the public entry point. It owns the transport and a model registry,
implements the :class:`~aura.query.builder.Executor` contract used by query builders,
maps server error frames to typed exceptions, and provides transactions. It performs no
network IO at import time or in ``__init__`` — only ``connect``/``close``/requests do IO.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable, Sequence
from types import TracebackType
from typing import Any

from .backends.auradb import _ERROR_CODE_MAP as _ERROR_CODE_MAP  # re-exported for compatibility
from .backends.auradb import ProtocolBackend, error_from_frame
from .backends.base import Backend, BackendResult
from .config import ClientConfig
from .errors import (
    AuraBackendCapabilityError,
    AuraClientClosedError,
    AuraConnectionError,
    AuraQueryError,
    AuraSchemaError,
    AuraServerError,
)
from .hydration.hydrator import Hydrator
from .models import AuraModel, get_model
from .observability import Metrics, TelemetryConfig, _TelemetryBridge
from .protocol.frames import Frame
from .protocol.opcodes import PROTOCOL_VERSION
from .query.ast import InsertQuery, QueryNode, RawQuery, UpsertQuery
from .query.builder import (
    DeleteBuilder,
    QueryBuilder,
    QueryResult,
    TraverseBuilder,
    UpdateBuilder,
)
from .query.expressions import FieldReference
from .transport.base import Transport

__all__ = ["Aura", "Client", "Transaction", "connect"]

_MUTATION_OPS = frozenset({"insert", "update", "delete", "upsert"})


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

    def transaction(self, *, isolation: str = "serializable") -> Transaction:
        self._ensure_open()
        self._tx_counter += 1
        return Transaction(self, self._tx_counter, isolation)

    # -- dynamic model access ----------------------------------------------------
    def __getattr__(self, name: str) -> QueryBuilder:
        # Only called for attributes not found normally; resolve registered models.
        registry = self.__dict__.get("_registry", {})
        if name in registry:
            return QueryBuilder(registry[name], self.__dict__["_root_executor"])
        raise AttributeError(name)

    # -- Executor implementation -------------------------------------------------
    async def _execute(self, node: QueryNode, model: type[AuraModel], txid: int) -> QueryResult:
        self._ensure_open()
        ir = node.to_ir()
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
    discarded on error.
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
