"""The AuraDB backend: the native, high-performance path over the Aura Wire Protocol.

This backend wraps a :class:`~aura.transport.base.Transport` and speaks the binary Aura
Wire Protocol: it encodes Query IR into frames, correlates each request with its response,
maps server error frames to the typed Aura exception taxonomy, drives real server-side
cursors for streaming, and carries transactions by id. It is the reference implementation
the SQL/document/key-value adapters are measured against, and the only backend that
declares the ``native_protocol`` capability.

The in-memory reference engine reached through ``aura+memory://`` and ``memory://`` runs
this exact protocol path in-process (see :class:`aura.backends.memory.MemoryBackend`),
so the native code path is exercised with no network or external service.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from ..config import ClientConfig
from ..errors import (
    AuraAuthenticationError,
    AuraAuthorizationError,
    AuraConstraintError,
    AuraError,
    AuraNotFoundError,
    AuraNotLeaderError,
    AuraProtocolError,
    AuraQueryError,
    AuraSchemaError,
    AuraServerError,
    AuraValidationError,
)
from ..observability import Metrics, _TelemetryBridge
from ..protocol.frames import Frame
from ..protocol.messages import (
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
from ..protocol.opcodes import PROTOCOL_VERSION, Opcode
from ..schema.compiler import schema_document
from ..schema.migrations import MigrationPlan, diff_schemas
from .base import Backend, BackendResult
from .capabilities import BackendCapabilities

if TYPE_CHECKING:
    from ..models import AuraModel
    from ..transport.base import Transport

__all__ = ["AuraDBBackend", "ProtocolBackend", "error_from_frame", "_ERROR_CODE_MAP"]

#: Maps a server error ``code`` to the specific Aura exception class it raises. Unknown
#: codes fall back to :class:`AuraServerError`.
_ERROR_CODE_MAP: dict[str, type[AuraError]] = {
    "validation_error": AuraValidationError,
    "query_error": AuraQueryError,
    "schema_error": AuraSchemaError,
    "not_found": AuraNotFoundError,
    "constraint_violation": AuraConstraintError,
    "authentication_error": AuraAuthenticationError,
    "authorization_error": AuraAuthorizationError,
    "not_leader": AuraNotLeaderError,
    "protocol_error": AuraProtocolError,
    "server_error": AuraServerError,
}

_MUTATION_OPS = frozenset({"insert", "update", "delete", "upsert"})


def error_from_frame(frame: Frame) -> AuraError:
    """Build the typed :class:`AuraError` described by an ``ERROR`` frame (no side effects)."""
    body = ErrorBody.from_payload(decode_body(frame.payload))
    if body.code == "not_leader":
        # Leader-routing hints ride in the error context (and/or a nested
        # ``not_leader`` object); surface them on the dedicated exception.
        return AuraNotLeaderError.from_server_payload(
            body.message or "not leader",
            code=body.code,
            retryable=body.retryable,
            request_id=frame.request_id,
            payload=body.context,
        )
    error_cls = _ERROR_CODE_MAP.get(body.code, AuraServerError)
    return error_cls(
        body.message or "Server error",
        code=body.code,
        retryable=body.retryable,
        request_id=frame.request_id,
        context=body.context,
    )


_AURADB_CAPABILITIES = BackendCapabilities(
    name="auradb",
    transactions=True,
    schema_create=True,
    schema_migrations=True,
    json_fields=True,
    relationships=True,
    graph_traversal=True,
    vector_search=True,
    hybrid_search=True,
    full_text_search=True,
    raw_queries=True,
    explain=True,
    server_side_cursors=True,
    native_protocol=True,
    document_queries=True,
    key_value=False,
    group_by=True,
    query_profile=True,
    hnsw_preview=True,
    cursor_resume=True,
)


class ProtocolBackend(Backend):
    """Backend that executes Query IR over the Aura Wire Protocol via a transport.

    It owns the transport, performs the handshake and schema registration, and records the
    protocol-level observability metrics (serialize time, request latency, bytes, frames,
    retries) on the shared :class:`~aura.observability.Metrics` instance. The client records
    the higher-level metrics (per-operation query counts, hydration time).
    """

    name = "auradb"

    def __init__(
        self,
        config: ClientConfig,
        transport: Transport,
        metrics: Metrics,
        telemetry: _TelemetryBridge,
    ) -> None:
        self._config = config
        self._transport = transport
        self._metrics = metrics
        self._telemetry = telemetry
        self._request_counter = 0

    @property
    def transport(self) -> Transport:
        return self._transport

    def capabilities(self) -> BackendCapabilities:
        return _AURADB_CAPABILITIES

    # -- lifecycle ---------------------------------------------------------------
    async def connect(self) -> None:
        await self._transport.connect()
        await self._handshake()

    async def close(self) -> None:
        await self._transport.close()

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
            self._raise(response)
        if response.opcode is not Opcode.HANDSHAKE_ACK:
            raise AuraProtocolError("Server did not acknowledge handshake")

    async def ping(self) -> bool:
        nonce = self._next_request_id()
        frame = Frame(
            opcode=Opcode.PING,
            payload=encode_body(PingRequest(nonce=nonce).to_payload()),
            request_id=nonce,
        )
        response = await self._send_frame(frame, "ping")
        if response.opcode is Opcode.ERROR:
            self._raise(response)
        pong = PongResponse.from_payload(decode_body(response.payload))
        return pong.nonce == nonce

    # -- schema ------------------------------------------------------------------
    async def register_schema(self, models: Iterable[type[AuraModel]]) -> None:
        document = schema_document(models)
        frame = Frame(
            opcode=Opcode.SCHEMA,
            payload=encode_body({"models": document["models"]}),
            request_id=self._next_request_id(),
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise(response)

    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        # On AuraDB, registering the schema document is how the server materializes models.
        await self.register_schema(models)

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        # Dropping live models is a destructive server operation, not a client one.
        self._require("schema_migrations")

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        current = await self.server_schema()
        return diff_schemas(current, schema_document(models))

    async def server_schema(self) -> list[dict[str, Any]]:
        frame = Frame(
            opcode=Opcode.SCHEMA,
            payload=encode_body(SchemaRequest().to_payload()),
            request_id=self._next_request_id(),
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise(response)
        return list(decode_body(response.payload).get("models", []))

    # -- execution ---------------------------------------------------------------
    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        return await self._execute(ir, Opcode.QUERY, txid)

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        return await self._execute(ir, Opcode.MUTATION, txid)

    async def _execute(self, ir: dict[str, Any], opcode: Opcode, txid: int) -> BackendResult:
        operation = str(ir.get("operation", ""))
        body: Any = MutationRequest(ir=ir) if opcode is Opcode.MUTATION else QueryRequest(ir=ir)
        serialize_start = time.perf_counter()
        payload = encode_body(body.to_payload())
        self._metrics.serialize_latency.record(time.perf_counter() - serialize_start)

        frame = Frame(
            opcode=opcode,
            payload=payload,
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, operation, ir)
        if response.opcode is Opcode.ERROR:
            self._raise(response)
        return self._to_result(response, operation)

    def _to_result(self, response: Frame, operation: str) -> BackendResult:
        payload = decode_body(response.payload)
        if response.opcode is Opcode.MUTATION_RESULT:
            mbody = MutationResultBody.from_payload(payload)
            return BackendResult(
                rows=[dict(r) for r in mbody.returning],
                affected=mbody.affected,
                request_id=response.request_id,
            )
        if response.opcode is not Opcode.QUERY_RESULT:
            raise AuraProtocolError(
                f"Unexpected response opcode {response.opcode!r} for {operation}"
            )
        result = QueryResultBody.from_payload(payload)
        return BackendResult(
            rows=[dict(r) for r in result.rows],
            count=result.count,
            metadata=dict(result.metadata),
            request_id=response.request_id,
        )

    # -- streaming ---------------------------------------------------------------
    async def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        operation = str(ir.get("operation", "select"))
        cursor_ir = {**ir, "cursor": {"batch_size": batch_size}}
        serialize_start = time.perf_counter()
        payload = encode_body(QueryRequest(ir=cursor_ir).to_payload())
        self._metrics.serialize_latency.record(time.perf_counter() - serialize_start)
        frame = Frame(
            opcode=Opcode.QUERY,
            payload=payload,
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, operation, cursor_ir)
        page, token, has_more = self._interpret_page(response)
        try:
            while True:
                for row in page:
                    yield row
                if not token or not has_more:
                    token = None
                    break
                page, token, has_more = await self._cursor_fetch(token, batch_size, txid)
        finally:
            # On full drain ``token`` is cleared; on early break/aclose a GeneratorExit
            # unwinds through here with a live token, so the server cursor is released.
            if token:
                await self._cursor_release(token, txid)

    async def _cursor_fetch(
        self, token: str, batch_size: int, txid: int
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        frame = Frame(
            opcode=Opcode.CURSOR_FETCH,
            payload=encode_body(CursorFetch(cursor=token, batch_size=batch_size).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, "cursor_fetch")
        return self._interpret_page(response)

    async def _cursor_release(self, token: str, txid: int) -> None:
        frame = Frame(
            opcode=Opcode.CURSOR_CLOSE,
            payload=encode_body(CursorClose(cursor=token).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._send_frame(frame, "cursor_close")
        if response.opcode is Opcode.ERROR:
            self._raise(response)

    def _interpret_page(self, response: Frame) -> tuple[list[dict[str, Any]], str | None, bool]:
        if response.opcode is Opcode.ERROR:
            self._raise(response)
        if response.opcode is not Opcode.QUERY_RESULT:
            raise AuraProtocolError(f"Unexpected response opcode {response.opcode!r} for stream")
        result = QueryResultBody.from_payload(decode_body(response.payload))
        rows = [dict(r) for r in result.rows]
        token = result.metadata.get("cursor")
        has_more = bool(result.metadata.get("has_more"))
        return rows, (str(token) if token else None), has_more

    # -- transactions ------------------------------------------------------------
    async def begin(self, txid: int, isolation: str) -> None:
        await self._tx_control(Opcode.BEGIN_TX, txid, "begin", isolation)

    async def commit(self, txid: int, isolation: str) -> None:
        await self._tx_control(Opcode.COMMIT_TX, txid, "commit", isolation)

    async def rollback(self, txid: int, isolation: str) -> None:
        await self._tx_control(Opcode.ROLLBACK_TX, txid, "rollback", isolation)

    async def _tx_control(self, opcode: Opcode, txid: int, action: str, isolation: str) -> None:
        frame = Frame(
            opcode=opcode,
            payload=encode_body(TxControl(action=action, isolation=isolation).to_payload()),
            request_id=self._next_request_id(),
            transaction_id=txid,
        )
        response = await self._request(frame)
        if response.opcode is Opcode.ERROR:
            self._raise(response)

    # -- transport plumbing ------------------------------------------------------
    async def _send_frame(
        self, frame: Frame, operation: str, ir: dict[str, Any] | None = None
    ) -> Frame:
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

    def _raise(self, frame: Frame) -> None:
        error = error_from_frame(frame)
        self._metrics.record_error(error.code)
        raise error

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "transport": self._transport.describe(),
            "protocol_version": PROTOCOL_VERSION,
        }

    def stats(self) -> dict[str, int]:
        return self._transport.stats.as_dict()

    def _next_request_id(self) -> int:
        self._request_counter = (self._request_counter + 1) % (2**63)
        return self._request_counter or 1


class AuraDBBackend(ProtocolBackend):
    """The native AuraDB backend over a TCP (or TLS) Aura Wire Protocol transport."""

    name = "auradb"
