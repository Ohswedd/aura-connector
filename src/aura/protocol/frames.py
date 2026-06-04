"""The Aura Wire Protocol frame.

A frame is a fixed 32-byte binary header followed by an optional payload (and an
optional trailing payload checksum). The header layout (big-endian) is::

    offset  size  field
    0       4     magic ("AURA")
    4       1     version
    5       1     opcode
    6       1     flags
    7       1     compression
    8       4     payload_length
    12      8     transaction_id
    20      8     request_id
    28      4     header_checksum (CRC32 of bytes 0..28)

This module defines the in-memory :class:`Frame`; encoding/decoding lives in
:mod:`aura.protocol.codec`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .._native import crc32 as _crc32
from .opcodes import HEADER_SIZE, MAGIC, PROTOCOL_VERSION, Compression, Flags, Opcode

__all__ = ["HEADER_STRUCT", "Frame", "header_checksum"]

#: ``struct`` format for the fixed header (big-endian).
HEADER_STRUCT = struct.Struct(">4sBBBBIQQI")
assert HEADER_STRUCT.size == HEADER_SIZE


def header_checksum(header_without_crc: bytes) -> int:
    """CRC32 of the first 28 header bytes (the field protecting header integrity).

    Routed through :mod:`aura._native`, which uses the optional compiled backend
    when available and an identical pure-Python CRC32 otherwise.
    """
    return _crc32(header_without_crc)


@dataclass
class Frame:
    """A decoded protocol frame.

    ``payload`` always holds the *decompressed* body. Compression and checksum are
    applied/verified by the codec; the in-memory frame exposes logical content.
    """

    opcode: Opcode
    payload: bytes = b""
    request_id: int = 0
    transaction_id: int = 0
    flags: Flags = Flags.NONE
    compression: Compression = Compression.NONE
    version: int = PROTOCOL_VERSION
    magic: bytes = field(default=MAGIC)

    def with_payload(self, payload: bytes) -> Frame:
        return Frame(
            opcode=self.opcode,
            payload=payload,
            request_id=self.request_id,
            transaction_id=self.transaction_id,
            flags=self.flags,
            compression=self.compression,
            version=self.version,
            magic=self.magic,
        )

    @property
    def is_error(self) -> bool:
        return self.opcode is Opcode.ERROR
