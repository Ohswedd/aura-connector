"""Typed message bodies carried inside protocol frames.

Bodies are encoded as canonical (sorted-key) JSON so the wrapped payload is itself
deterministic. Each message maps to an :class:`~aura.protocol.opcodes.Opcode`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..errors import AuraProtocolError, AuraSerializationError
from .opcodes import Opcode

__all__ = [
    "CursorAck",
    "CursorClose",
    "CursorFetch",
    "ErrorBody",
    "HandshakeAck",
    "HandshakeRequest",
    "MutationRequest",
    "MutationResultBody",
    "PingRequest",
    "PongResponse",
    "QueryRequest",
    "QueryResultBody",
    "SchemaRequest",
    "SchemaResponse",
    "TxAck",
    "TxControl",
    "decode_body",
    "encode_body",
]


def encode_body(payload: dict[str, Any]) -> bytes:
    """Serialize a body dict to canonical JSON bytes."""
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuraSerializationError(f"Cannot encode payload body: {exc}") from exc


def decode_body(data: bytes) -> dict[str, Any]:
    """Parse canonical JSON body bytes into a dict."""
    if not data:
        return {}
    try:
        result = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuraProtocolError(f"Malformed payload body: {exc}") from exc
    if not isinstance(result, dict):
        raise AuraProtocolError("Payload body must be a JSON object")
    return result


@dataclass(frozen=True)
class HandshakeRequest:
    opcode = Opcode.HANDSHAKE
    client_version: int = 1
    features: list[str] = field(default_factory=list)
    auth: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "client_version": self.client_version,
            "features": sorted(self.features),
            "auth": dict(self.auth),
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> HandshakeRequest:
        return cls(
            client_version=int(data.get("client_version", 1)),
            features=list(data.get("features", [])),
            auth=dict(data.get("auth", {})),
        )


@dataclass(frozen=True)
class HandshakeAck:
    opcode = Opcode.HANDSHAKE_ACK
    server_version: int = 1
    features: list[str] = field(default_factory=list)
    compression: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "server_version": self.server_version,
            "features": sorted(self.features),
            "compression": sorted(self.compression),
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> HandshakeAck:
        return cls(
            server_version=int(data.get("server_version", 1)),
            features=list(data.get("features", [])),
            compression=list(data.get("compression", [])),
        )


@dataclass(frozen=True)
class PingRequest:
    opcode = Opcode.PING
    nonce: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {"nonce": self.nonce}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> PingRequest:
        return cls(nonce=int(data.get("nonce", 0)))


@dataclass(frozen=True)
class PongResponse:
    opcode = Opcode.PONG
    nonce: int = 0
    server_version: int = 1

    def to_payload(self) -> dict[str, Any]:
        return {"nonce": self.nonce, "server_version": self.server_version}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> PongResponse:
        return cls(
            nonce=int(data.get("nonce", 0)),
            server_version=int(data.get("server_version", 1)),
        )


@dataclass(frozen=True)
class SchemaRequest:
    opcode = Opcode.SCHEMA

    def to_payload(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> SchemaRequest:
        return cls()


@dataclass(frozen=True)
class SchemaResponse:
    opcode = Opcode.SCHEMA_RESULT
    models: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"models": self.models}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> SchemaResponse:
        return cls(models=list(data.get("models", [])))


@dataclass(frozen=True)
class QueryRequest:
    opcode = Opcode.QUERY
    ir: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"ir": self.ir}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> QueryRequest:
        return cls(ir=dict(data.get("ir", {})))


@dataclass(frozen=True)
class QueryResultBody:
    opcode = Opcode.QUERY_RESULT
    rows: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"rows": self.rows, "metadata": self.metadata}
        if self.count is not None:
            payload["count"] = self.count
        return payload

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> QueryResultBody:
        return cls(
            rows=list(data.get("rows", [])),
            count=data.get("count"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class MutationRequest:
    opcode = Opcode.MUTATION
    ir: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"ir": self.ir}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> MutationRequest:
        return cls(ir=dict(data.get("ir", {})))


@dataclass(frozen=True)
class MutationResultBody:
    opcode = Opcode.MUTATION_RESULT
    affected: int = 0
    returning: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"affected": self.affected, "returning": self.returning}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> MutationResultBody:
        return cls(
            affected=int(data.get("affected", 0)),
            returning=list(data.get("returning", [])),
        )


@dataclass(frozen=True)
class ErrorBody:
    opcode = Opcode.ERROR
    code: str = "server_error"
    message: str = ""
    retryable: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "context": dict(self.context),
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> ErrorBody:
        return cls(
            code=str(data.get("code", "server_error")),
            message=str(data.get("message", "")),
            retryable=bool(data.get("retryable", False)),
            context=dict(data.get("context", {})),
        )


@dataclass(frozen=True)
class CursorFetch:
    """Request the next page of an open server cursor."""

    opcode = Opcode.CURSOR_FETCH
    cursor: str = ""
    batch_size: int = 1000

    def to_payload(self) -> dict[str, Any]:
        return {"cursor": self.cursor, "batch_size": self.batch_size}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> CursorFetch:
        return cls(
            cursor=str(data.get("cursor", "")),
            batch_size=int(data.get("batch_size", 1000)),
        )


@dataclass(frozen=True)
class CursorClose:
    """Release an open server cursor early."""

    opcode = Opcode.CURSOR_CLOSE
    cursor: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {"cursor": self.cursor}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> CursorClose:
        return cls(cursor=str(data.get("cursor", "")))


@dataclass(frozen=True)
class CursorAck:
    """Server acknowledgement that a cursor was released."""

    opcode = Opcode.CURSOR_ACK
    cursor: str = ""
    status: str = "closed"

    def to_payload(self) -> dict[str, Any]:
        return {"cursor": self.cursor, "status": self.status}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> CursorAck:
        return cls(
            cursor=str(data.get("cursor", "")),
            status=str(data.get("status", "closed")),
        )


@dataclass(frozen=True)
class TxControl:
    """Begin/commit/rollback control message; opcode set by the sender."""

    action: str = "begin"
    # AuraDB applies snapshot isolation with optimistic conflict detection regardless of
    # this token; "snapshot" is the honest default and "serializable" is not claimed.
    isolation: str = "snapshot"

    def to_payload(self) -> dict[str, Any]:
        return {"action": self.action, "isolation": self.isolation}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> TxControl:
        return cls(
            action=str(data.get("action", "begin")),
            isolation=str(data.get("isolation", "snapshot")),
        )


@dataclass(frozen=True)
class TxAck:
    opcode = Opcode.TX_ACK
    transaction_id: int = 0
    status: str = "ok"

    def to_payload(self) -> dict[str, Any]:
        return {"transaction_id": self.transaction_id, "status": self.status}

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> TxAck:
        return cls(
            transaction_id=int(data.get("transaction_id", 0)),
            status=str(data.get("status", "ok")),
        )
