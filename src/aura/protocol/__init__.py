"""Aura Wire Protocol: opcodes, frames, codec, and typed messages."""

from __future__ import annotations

from .codec import decode_frame, encode_frame, try_decode_frame
from .frames import Frame
from .messages import (
    ErrorBody,
    HandshakeAck,
    HandshakeRequest,
    MutationRequest,
    MutationResultBody,
    PingRequest,
    PongResponse,
    QueryRequest,
    QueryResultBody,
    SchemaRequest,
    SchemaResponse,
    TxAck,
    TxControl,
    decode_body,
    encode_body,
)
from .opcodes import (
    HEADER_SIZE,
    MAGIC,
    PROTOCOL_VERSION,
    Compression,
    Flags,
    Opcode,
)

__all__ = [
    "HEADER_SIZE",
    "MAGIC",
    "PROTOCOL_VERSION",
    "Compression",
    "ErrorBody",
    "Flags",
    "Frame",
    "HandshakeAck",
    "HandshakeRequest",
    "MutationRequest",
    "MutationResultBody",
    "Opcode",
    "PingRequest",
    "PongResponse",
    "QueryRequest",
    "QueryResultBody",
    "SchemaRequest",
    "SchemaResponse",
    "TxAck",
    "TxControl",
    "decode_body",
    "decode_frame",
    "encode_body",
    "encode_frame",
    "try_decode_frame",
]
