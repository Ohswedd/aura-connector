"""Deterministic frame encoder/decoder.

Encoding is byte-for-byte deterministic for a given frame and options, which makes the
format golden-testable. The decoder validates the magic bytes, protocol version,
header checksum, payload-length limit, and optional payload checksum, and fails closed
on any violation.
"""

from __future__ import annotations

import struct
import zlib

from .._native import crc32 as _crc32
from ..config import DEFAULT_MAX_PAYLOAD_BYTES
from ..errors import AuraProtocolError, AuraProtocolVersionError
from .frames import Frame, header_checksum
from .opcodes import HEADER_SIZE, MAGIC, PROTOCOL_VERSION, Compression, Flags, Opcode

__all__ = ["decode_frame", "encode_frame", "try_decode_frame"]

_HEADER_NO_CRC = struct.Struct(">4sBBBBIQQ")  # 28 bytes, everything before the checksum
_CRC = struct.Struct(">I")
_KNOWN_OPCODES = frozenset(int(o) for o in Opcode)


def encode_frame(
    frame: Frame,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    compress: bool = False,
    payload_checksum: bool = False,
) -> bytes:
    """Serialize ``frame`` into wire bytes.

    ``compress`` zlib-compresses the body; ``payload_checksum`` appends a CRC32 of the
    (possibly compressed) body. The header always carries a CRC32 of its first 28
    bytes. Raises :class:`AuraProtocolError` if the body exceeds ``max_payload_bytes``.
    """
    body = frame.payload
    flags = Flags(frame.flags)
    compression = Compression.NONE

    if compress and body:
        body = zlib.compress(body)
        compression = Compression.ZLIB
        flags |= Flags.COMPRESSED
    if payload_checksum:
        flags |= Flags.PAYLOAD_CHECKSUM

    if len(body) > max_payload_bytes:
        raise AuraProtocolError(
            f"Encoded payload {len(body)} bytes exceeds limit {max_payload_bytes}",
            context={"size": len(body), "limit": max_payload_bytes},
        )

    header_no_crc = _HEADER_NO_CRC.pack(
        MAGIC,
        frame.version,
        int(frame.opcode),
        int(flags),
        int(compression),
        len(body),
        frame.transaction_id,
        frame.request_id,
    )
    header = header_no_crc + _CRC.pack(header_checksum(header_no_crc))

    if flags & Flags.PAYLOAD_CHECKSUM:
        return header + body + _CRC.pack(_crc32(body))
    return header + body


def try_decode_frame(
    data: bytes, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
) -> tuple[Frame | None, int]:
    """Attempt to decode one frame from the front of ``data``.

    Returns ``(frame, bytes_consumed)`` on success, or ``(None, 0)`` if ``data`` does
    not yet contain a complete frame. Raises :class:`AuraProtocolError` for malformed
    frames. Suitable for incremental stream parsing.
    """
    if len(data) < HEADER_SIZE:
        return None, 0

    header_no_crc = data[: _HEADER_NO_CRC.size]
    (magic, version, opcode_int, flags_int, compression_int, payload_len, txid, reqid) = (
        _HEADER_NO_CRC.unpack(header_no_crc)
    )
    (stored_header_crc,) = _CRC.unpack(data[_HEADER_NO_CRC.size : HEADER_SIZE])

    if magic != MAGIC:
        raise AuraProtocolError("Invalid magic bytes; not an Aura frame")
    if version != PROTOCOL_VERSION:
        raise AuraProtocolVersionError(
            f"Unsupported protocol version {version}; client speaks {PROTOCOL_VERSION}",
            context={"server_version": version, "client_version": PROTOCOL_VERSION},
        )
    if header_checksum(header_no_crc) != stored_header_crc:
        raise AuraProtocolError("Header checksum mismatch; frame corrupted")
    if opcode_int not in _KNOWN_OPCODES:
        raise AuraProtocolError(f"Unknown opcode {opcode_int}")
    if payload_len > max_payload_bytes:
        raise AuraProtocolError(
            f"Declared payload {payload_len} bytes exceeds limit {max_payload_bytes}",
            context={"size": payload_len, "limit": max_payload_bytes},
        )

    flags = Flags(flags_int)
    try:
        compression = Compression(compression_int)
    except ValueError as exc:
        raise AuraProtocolError(f"Unknown compression marker {compression_int}") from exc

    trailer = _CRC.size if flags & Flags.PAYLOAD_CHECKSUM else 0
    total = HEADER_SIZE + payload_len + trailer
    if len(data) < total:
        return None, 0

    body = data[HEADER_SIZE : HEADER_SIZE + payload_len]

    if flags & Flags.PAYLOAD_CHECKSUM:
        (stored_payload_crc,) = _CRC.unpack(
            data[HEADER_SIZE + payload_len : HEADER_SIZE + payload_len + _CRC.size]
        )
        if _crc32(body) != stored_payload_crc:
            raise AuraProtocolError("Payload checksum mismatch; frame corrupted")

    if compression is Compression.ZLIB:
        try:
            body = zlib.decompress(body)
        except zlib.error as exc:
            raise AuraProtocolError("Failed to decompress payload") from exc

    frame = Frame(
        opcode=Opcode(opcode_int),
        payload=body,
        request_id=reqid,
        transaction_id=txid,
        flags=flags & ~Flags.COMPRESSED,
        compression=Compression.NONE,
        version=version,
    )
    return frame, total


def decode_frame(
    data: bytes, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
) -> tuple[Frame, int]:
    """Decode exactly one complete frame; raise if ``data`` is incomplete."""
    frame, consumed = try_decode_frame(data, max_payload_bytes=max_payload_bytes)
    if frame is None:
        raise AuraProtocolError("Incomplete frame: not enough bytes to decode")
    return frame, consumed
