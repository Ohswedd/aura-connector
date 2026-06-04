"""Aura Wire Protocol version 1 (AWP 1) codec for the native AuraDB backend.

This is the framing spoken by the AuraDB single-node server (``auradb-protocol``).
It is distinct from the in-process reference protocol used by the memory backend:
AWP 1 uses a 44-byte header with a 128-bit request id and CRC32 integrity checks.

Frame layout (big-endian):

====== ===== ================================================
offset size  field
====== ===== ================================================
0      4     magic ("AURA")
4      1     version (1)
5      1     opcode
6      2     flags (u16)
8      2     header length (u16, always 44)
10     1     compression (0 = none)
11     1     reserved (0)
12     4     payload length (u32)
16     16    request id (u128)
32     8     transaction id (u64)
40     4     header checksum (CRC32 of bytes 0..40)
44     ...   payload
[+4]         payload checksum (CRC32 of payload) when FLAG_PAYLOAD_CHECKSUM is set
====== ===== ================================================
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

__all__ = [
    "MAGIC",
    "PROTOCOL_VERSION",
    "HEADER_LEN",
    "FLAG_PAYLOAD_CHECKSUM",
    "Opcode",
    "AWPFrame",
    "encode_frame",
    "decode_frame",
]

MAGIC = b"AURA"
PROTOCOL_VERSION = 1
HEADER_LEN = 44
FLAG_PAYLOAD_CHECKSUM = 0x0001
_HEADER_CHECKSUM_OFFSET = 40


class Opcode(IntEnum):
    """AWP 1 opcodes (must match ``auradb-protocol``)."""

    HELLO = 0x01
    PING = 0x02
    HEALTH = 0x03
    AUTH = 0x04
    SCHEMA_CREATE = 0x10
    SCHEMA_DROP = 0x11
    SCHEMA_GET = 0x12
    SCHEMA_LIST = 0x13
    QUERY = 0x20
    MUTATE = 0x21
    CURSOR_FETCH = 0x22
    CURSOR_CLOSE = 0x23
    EXPLAIN = 0x24
    MIGRATION_ESTIMATE = 0x25
    TXN_BEGIN = 0x30
    TXN_COMMIT = 0x31
    TXN_ROLLBACK = 0x32
    HELLO_ACK = 0x81
    PONG = 0x82
    HEALTH_RESULT = 0x83
    AUTH_RESULT = 0x84
    OK = 0x90
    QUERY_RESULT = 0x91
    ERROR = 0xFF


@dataclass(frozen=True)
class AWPFrame:
    """A decoded AWP 1 frame."""

    opcode: int
    payload: bytes = b""
    request_id: int = 0
    transaction_id: int = 0


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode_frame(frame: AWPFrame) -> bytes:
    """Encode a frame to AWP 1 wire bytes (always with a payload checksum)."""
    header = bytearray()
    header += MAGIC
    header.append(PROTOCOL_VERSION)
    header.append(int(frame.opcode))
    header += struct.pack(">H", FLAG_PAYLOAD_CHECKSUM)
    header += struct.pack(">H", HEADER_LEN)
    header.append(0)  # compression: none
    header.append(0)  # reserved
    header += struct.pack(">I", len(frame.payload))
    header += int(frame.request_id).to_bytes(16, "big")
    header += struct.pack(">Q", int(frame.transaction_id))
    header += struct.pack(">I", _crc32(bytes(header)))
    return bytes(header) + frame.payload + struct.pack(">I", _crc32(frame.payload))


def decode_header(header: bytes) -> tuple[int, int, int]:
    """Parse a 44-byte header, returning ``(opcode, payload_len, trailer_len)``.

    Raises ``ValueError`` on a malformed header (bad magic, version, or checksum).
    """
    if len(header) != HEADER_LEN:
        raise ValueError(f"header must be {HEADER_LEN} bytes, got {len(header)}")
    if header[0:4] != MAGIC:
        raise ValueError("bad magic bytes")
    version = header[4]
    if version == 0 or version > PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {version}")
    opcode = header[5]
    flags = struct.unpack(">H", header[6:8])[0]
    payload_len = struct.unpack(">I", header[12:16])[0]
    stored = struct.unpack(">I", header[40:44])[0]
    if stored != _crc32(header[:_HEADER_CHECKSUM_OFFSET]):
        raise ValueError("header checksum mismatch")
    trailer = 4 if flags & FLAG_PAYLOAD_CHECKSUM else 0
    return opcode, payload_len, trailer


def decode_frame(data: bytes) -> AWPFrame:
    """Decode exactly one complete frame from ``data`` (header + payload [+ checksum])."""
    opcode, payload_len, trailer = decode_header(data[:HEADER_LEN])
    request_id = int.from_bytes(data[16:32], "big")
    transaction_id = struct.unpack(">Q", data[32:40])[0]
    payload = data[HEADER_LEN : HEADER_LEN + payload_len]
    if trailer:
        off = HEADER_LEN + payload_len
        stored = struct.unpack(">I", data[off : off + 4])[0]
        if stored != _crc32(payload):
            raise ValueError("payload checksum mismatch")
    return AWPFrame(
        opcode=opcode,
        payload=payload,
        request_id=request_id,
        transaction_id=transaction_id,
    )
