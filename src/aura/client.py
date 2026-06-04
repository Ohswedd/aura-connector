"""The Aura client: async lifecycle, model registry, and query execution.

``Client`` is the public entry point. It owns the transport and a model registry,
implements the :class:`~aura.query.builder.Executor` contract used by query builders,
maps server error frames to typed exceptions, and provides transactions. It performs no
network IO at import time or in ``__init__`` — only ``connect``/``close``/requests do IO.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from types import TracebackType
from typing import Any

from .config import ClientConfig, parse_dsn
from .errors import (
    AuraAuthenticationError,
    AuraAuthorizationError,
    AuraClientClosedError,
    AuraConnectionError,
    AuraConstraintError,
    AuraError,
    AuraNotFoundError,
    AuraProtocolError,
    AuraQueryError,
    AuraSchemaError,
    AuraServerError,
    AuraValidationError,
)
from .hydration.hydrator import Hydrator
from .models import AuraModel, get_model
from .observability import Metrics, TelemetryConfig, _TelemetryBridge
from .protocol.frames import Frame
from .protocol.messages import (
    CursorClose,
    CursorFetch,
    ErrorBody,
    HandshakeRequest,
    MutationRequest,
    MutationResultBody,
    PingRequest,
    PongResponse,
    QueryRequest,
    QueryResultBody,
    SchemaRequest,
    TxControl,
    decode_body,
    encode_body,
)
from .protocol.opcodes import PROTOCOL_VERSION, Opcode
from .query.ast import InsertQuery, QueryNode, RawQuery, UpsertQuery
from .query.builder import (
    DeleteBuilder,
    QueryBuilder,
    QueryResult,
    TraverseBuilder,
    UpdateBuilder,
)
from .query.expressions import FieldReference
from .schema.compiler import schema_document
from .transport.base import Transport
from .transport.memory import MemoryTransport
from .transport.tcp import TCPTransport

__all__ = ["Aura", "Client", "Transaction", "connect"]

_MUTATION_OPS = frozenset({"insert", "update", "delete", "upsert"})

_ERROR_CODE_MAP: dict[str, type[AuraError]] = {
    "validation_error": AuraValidationError,
    "query_error": AuraQueryError,
    "schema_error": AuraSchemaError,
    "not_found": AuraNotFoundError,
    "constraint_violation": AuraConstraintError,
    "authentication_error": AuraAuthenticationError,
    "authorization_error": AuraAuthorizationError,
    "protocol_error": AuraProtocolError,
    "server_error": AuraServerError,
}


def _make_transport(config: ClientConfig) -> Transport:
    if config.is_memory:
        return MemoryTransport(max_payload_bytes=config.max_payload_bytes)
    return TCPTransport(config)


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
        transport: Transport,
        models: Iterable[type[AuraModel]] = (),
        *,
        telemetry: Any = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._registry: dict[str, type[AuraModel]] = {}
        self._hydrator = Hydrator()
        self._closed = False
        self._opened = False
        self._request_counter = 0
        self._tx_counter = 0
        self._root_executor = _BoundExecutor(self, 0)
        self._metrics = Metrics()
        self._telemetry = _TelemetryBridge(TelemetryConfig.from_value(telemetry))
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
        config = parse_dsn(dsn, **options)
        return _ClientConnector(cls, config, tuple(models), transport, telemetry)

    @property
    def config(self) -> ClientConfig:
        return self._config

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def metrics(self) -> Metrics:
        """In-process observability metrics for this client."""
        return self._metrics

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
        await self._transport.connect()
        await self._handshake()
        if self._registry:
            await self._register_schema()
        self._opened = True

    async def _handshake(self) -> None:
        auth = self._config.auth.headers() if self._config.auth else {}
        body = HandshakeRequest(client_version=PROTOCOL_VERSION, features=["query"], auth=auth)
        frame = Frame(
            opcode=Opcode.HANDSHAKE,
            payload=encode_body(body.to_payload()),
            request_id=self._next_request_id(),
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)
        if response.opcode is not Opcode.HANDSHAKE_ACK:
            raise AuraProtocolError("Server did not acknowledge handshake")

    async def _register_schema(self) -> None:
        document = schema_document(self._registry.values())
        frame = Frame(
            opcode=Opcode.SCHEMA,
            payload=encode_body({"models": document["models"]}),
            request_id=self._next_request_id(),
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._transport.close()

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
        """Round-trip a ping; returns ``True`` on a matching pong."""
        self._ensure_open()
        nonce = self._next_request_id()
        frame = Frame(
            opcode=Opcode.PING,
            payload=encode_body(PingRequest(nonce=nonce).to_payload()),
            request_id=nonce,
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)
        pong = PongResponse.from_payload(decode_body(response.payload))
        return pong.nonce == nonce

    async def health(self) -> dict[str, Any]:
        """Return a non-sensitive health/diagnostic snapshot."""
        ok = await self.ping()
        return {
            "status": "ok" if ok else "degraded",
            "protocol_version": PROTOCOL_VERSION,
            "address": self._config.address,
            "transport": self._transport.describe(),
            "stats": self._transport.stats.as_dict(),
            "metrics": self._metrics.snapshot(),
            "models": sorted(self._registry),
        }

    async def server_schema(self) -> list[dict[str, Any]]:
        """Fetch the schema the server currently knows about."""
        self._ensure_open()
        frame = Frame(
            opcode=Opcode.SCHEMA,
            payload=encode_body(SchemaRequest().to_payload()),
            request_id=self._next_request_id(),
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)
        return list(decode_body(response.payload).get("models", []))

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
        is_mutation = node.operation in _MUTATION_OPS
        opcode = Opcode.MUTATION if is_mutation else Opcode.QUERY
        body = MutationRequest(ir=ir) if is_mutation else QueryRequest(ir=ir)

        serialize_start = time.perf_counter()
        payload = encode_body(body.to_payload())
        self._metrics.serialize_latency.record(time.perf_counter() - serialize_start)

        frame = Frame(
            opcode=opcode,
            payload=payload,
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, node.operation, ir)

        if response.opcode is Opcode.ERROR:
            self._raise_error(response)

        deserialize_start = time.perf_counter()
        result = self._interpret(node, model, response)
        self._metrics.deserialize_latency.record(time.perf_counter() - deserialize_start)
        return result

    async def _send_frame(
        self, frame: Frame, operation: str, ir: dict[str, Any] | None = None
    ) -> Frame:
        """Send a frame, recording the observability metric set and a telemetry span.

        Transport-level failures (raised as :class:`AuraError`) are counted before being
        re-raised; server error *frames* are counted by :meth:`_raise_error`.
        """
        self._metrics.record_query(operation)
        stats = self._transport.stats
        before_sent, before_recv = stats.bytes_sent, stats.bytes_received
        before_fsent, before_frecv = stats.frames_sent, stats.frames_received

        request_start = time.perf_counter()
        try:
            with self._telemetry.span(f"aura.{operation}", ir):
                response = await self._request(frame)
        except AuraError as exc:
            self._metrics.record_error(exc.code)
            raise
        self._metrics.request_latency.record(time.perf_counter() - request_start)
        self._metrics.record_bytes(
            stats.bytes_sent - before_sent, stats.bytes_received - before_recv
        )
        self._metrics.record_frames(
            stats.frames_sent - before_fsent, stats.frames_received - before_frecv
        )
        return response

    def _interpret(self, node: QueryNode, model: type[AuraModel], response: Frame) -> QueryResult:
        payload = decode_body(response.payload)
        operation = node.operation

        if response.opcode is Opcode.MUTATION_RESULT:
            body = MutationResultBody.from_payload(payload)
            rows = self._hydrator.hydrate_rows(model, body.returning) if body.returning else []
            return QueryResult(rows=rows, affected=body.affected, request_id=response.request_id)

        if response.opcode is not Opcode.QUERY_RESULT:
            raise AuraProtocolError(
                f"Unexpected response opcode {response.opcode!r} for {operation}"
            )

        result = QueryResultBody.from_payload(payload)
        if operation in {"count", "exists"}:
            return QueryResult(count=result.count, request_id=response.request_id)
        if operation == "raw":
            return QueryResult(rows=list(result.rows), request_id=response.request_id)
        if operation == "traverse":
            target_name = result.metadata.get("model", model.__name__)
            target = self._registry.get(target_name, model)
            rows = self._hydrator.hydrate_rows(target, result.rows)
            return QueryResult(rows=rows, request_id=response.request_id)

        partial = bool(getattr(node, "projection", ()))
        rows = self._hydrator.hydrate_rows(model, result.rows, partial=partial)
        return QueryResult(
            rows=rows,
            count=result.count,
            request_id=response.request_id,
            metadata=dict(result.metadata),
        )

    async def _stream(
        self, node: QueryNode, model: type[AuraModel], batch_size: int, txid: int
    ) -> AsyncIterator[Any]:
        """Yield rows page-by-page over a real server cursor.

        Iteration is bounded: the client requests a cursor, then pulls one ``batch_size``
        page at a time, holding at most one page in memory regardless of result size. If
        the consumer stops early (``break``), the open server cursor is released and the
        cancellation is counted; the client stays fully usable afterwards.
        """
        self._ensure_open()
        if batch_size <= 0:
            raise AuraQueryError("batch_size must be positive")

        cursor = await self._cursor_open(node, model, batch_size, txid)
        page, token, has_more = cursor
        cancelled = False
        try:
            while True:
                for row in page:
                    yield row
                if not token or not has_more:
                    token = None
                    break
                page, token, has_more = await self._cursor_fetch(model, token, batch_size, txid)
        except GeneratorExit:
            # Consumer cancelled the stream early.
            cancelled = True
            raise
        finally:
            if token:
                # A live cursor remains (early break or cancellation): release it.
                await self._cursor_release(token, txid)
            if cancelled:
                self._metrics.record_stream_cancellation()

    async def _cursor_open(
        self, node: QueryNode, model: type[AuraModel], batch_size: int, txid: int
    ) -> tuple[list[Any], str | None, bool]:
        ir = {**node.to_ir(), "cursor": {"batch_size": batch_size}}
        serialize_start = time.perf_counter()
        payload = encode_body(QueryRequest(ir=ir).to_payload())
        self._metrics.serialize_latency.record(time.perf_counter() - serialize_start)
        frame = Frame(
            opcode=Opcode.QUERY,
            payload=payload,
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, node.operation, ir)
        return self._interpret_page(model, response)

    async def _cursor_fetch(
        self, model: type[AuraModel], token: str, batch_size: int, txid: int
    ) -> tuple[list[Any], str | None, bool]:
        frame = Frame(
            opcode=Opcode.CURSOR_FETCH,
            payload=encode_body(CursorFetch(cursor=token, batch_size=batch_size).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, "cursor_fetch")
        return self._interpret_page(model, response)

    async def _cursor_release(self, token: str, txid: int) -> None:
        frame = Frame(
            opcode=Opcode.CURSOR_CLOSE,
            payload=encode_body(CursorClose(cursor=token).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, "cursor_close")
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)

    def _interpret_page(
        self, model: type[AuraModel], response: Frame
    ) -> tuple[list[Any], str | None, bool]:
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)
        if response.opcode is not Opcode.QUERY_RESULT:
            raise AuraProtocolError(f"Unexpected response opcode {response.opcode!r} for stream")
        deserialize_start = time.perf_counter()
        result = QueryResultBody.from_payload(decode_body(response.payload))
        rows = self._hydrator.hydrate_rows(model, result.rows)
        self._metrics.deserialize_latency.record(time.perf_counter() - deserialize_start)
        token = result.metadata.get("cursor")
        has_more = bool(result.metadata.get("has_more"))
        return rows, (str(token) if token else None), has_more

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

    # -- transaction control frames ----------------------------------------------
    async def _tx_control(self, opcode: Opcode, txid: int, action: str, isolation: str) -> None:
        frame = Frame(
            opcode=opcode,
            payload=encode_body(TxControl(action=action, isolation=isolation).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise_error(response)

    # -- low-level request with bounded retry ------------------------------------
    async def _request(self, frame: Frame) -> Frame:
        policy = self._config.retry
        last_error: AuraError | None = None
        for attempt in range(1, policy.max_attempts + 1):
            delay = policy.delay_for_attempt(attempt)
            if delay:
                await asyncio.sleep(delay)
            if attempt > 1:
                self._metrics.record_retry(1)
            try:
                return await self._transport.request(frame)
            except AuraError as exc:
                last_error = exc
                if not exc.retryable or attempt >= policy.max_attempts:
                    raise
        assert last_error is not None
        raise last_error

    def _raise_error(self, frame: Frame) -> None:
        body = ErrorBody.from_payload(decode_body(frame.payload))
        self._metrics.record_error(body.code)
        error_cls = _ERROR_CODE_MAP.get(body.code, AuraServerError)
        raise error_cls(
            body.message or "Server error",
            code=body.code,
            retryable=body.retryable,
            request_id=frame.request_id,
            context=body.context,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AuraClientClosedError("Client is closed")
        if not self._opened:
            raise AuraConnectionError("Client is not connected; use 'async with' or await connect")

    def _next_request_id(self) -> int:
        self._request_counter = (self._request_counter + 1) % (2**63)
        return self._request_counter or 1


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
        await self._client._tx_control(Opcode.BEGIN_TX, self._txid, "begin", self._isolation)

    async def commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._client._tx_control(Opcode.COMMIT_TX, self._txid, "commit", self._isolation)

    async def rollback(self) -> None:
        if self._finished:
            return
        self._finished = True
        await self._client._tx_control(Opcode.ROLLBACK_TX, self._txid, "rollback", self._isolation)

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

    __slots__ = ("_client", "_cls", "_config", "_models", "_telemetry", "_transport")

    def __init__(
        self,
        cls: type[Client],
        config: ClientConfig,
        models: tuple[type[AuraModel], ...],
        transport: Transport | None,
        telemetry: Any = None,
    ) -> None:
        self._cls = cls
        self._config = config
        self._models = models
        self._transport = transport
        self._telemetry = telemetry
        self._client: Client | None = None

    async def _create(self) -> Client:
        transport = self._transport or _make_transport(self._config)
        client = self._cls(self._config, transport, self._models, telemetry=self._telemetry)
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
