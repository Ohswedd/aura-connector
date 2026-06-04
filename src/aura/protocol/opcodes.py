"""Protocol constants: magic bytes, version, opcodes, flags, compression markers."""

from __future__ import annotations

from enum import IntEnum, IntFlag

__all__ = [
    "HEADER_SIZE",
    "MAGIC",
    "PROTOCOL_VERSION",
    "Compression",
    "Flags",
    "Opcode",
]

#: Four magic bytes identifying an Aura Wire Protocol frame.
MAGIC = b"AURA"

#: Current protocol version understood by this client.
PROTOCOL_VERSION = 1

#: Fixed binary header size in bytes.
HEADER_SIZE = 32


class Opcode(IntEnum):
    """Frame operation codes."""

    HANDSHAKE = 1
    HANDSHAKE_ACK = 2
    PING = 3
    PONG = 4
    QUERY = 5
    QUERY_RESULT = 6
    MUTATION = 7
    MUTATION_RESULT = 8
    SCHEMA = 9
    SCHEMA_RESULT = 10
    ERROR = 11
    STREAM_DATA = 12
    STREAM_END = 13
    BEGIN_TX = 14
    COMMIT_TX = 15
    ROLLBACK_TX = 16
    TX_ACK = 17
    CURSOR_FETCH = 18
    CURSOR_CLOSE = 19
    CURSOR_ACK = 20


class Flags(IntFlag):
    """Per-frame flags."""

    NONE = 0
    COMPRESSED = 1
    PAYLOAD_CHECKSUM = 2
    END_OF_STREAM = 4
    MORE_FRAMES = 8


class Compression(IntEnum):
    """Compression algorithm marker."""

    NONE = 0
    ZLIB = 1
