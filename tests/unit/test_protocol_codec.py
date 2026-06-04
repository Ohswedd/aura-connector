"""Tests for frame encoding/decoding."""

from __future__ import annotations

import pytest

from aura.errors import AuraProtocolError, AuraProtocolVersionError
from aura.protocol import Frame, Opcode, decode_frame, encode_frame, try_decode_frame
from aura.protocol.messages import QueryRequest, decode_body, encode_body
from aura.protocol.opcodes import HEADER_SIZE, MAGIC


def make_frame() -> Frame:
    body = encode_body(QueryRequest(ir={"operation": "select", "model": "User"}).to_payload())
    return Frame(opcode=Opcode.QUERY, payload=body, request_id=42, transaction_id=7)


def test_round_trip_plain() -> None:
    frame = make_frame()
    raw = encode_frame(frame)
    decoded, consumed = decode_frame(raw)
    assert consumed == len(raw)
    assert decoded.opcode is Opcode.QUERY
    assert decoded.request_id == 42
    assert decoded.transaction_id == 7
    assert decode_body(decoded.payload)["ir"]["model"] == "User"


def test_encoding_is_deterministic() -> None:
    frame = make_frame()
    assert encode_frame(frame) == encode_frame(frame)


def test_compression_round_trip() -> None:
    frame = make_frame()
    raw = encode_frame(frame, compress=True, payload_checksum=True)
    decoded, _ = decode_frame(raw)
    assert decode_body(decoded.payload)["ir"]["model"] == "User"


def test_payload_checksum_round_trip() -> None:
    frame = make_frame()
    raw = encode_frame(frame, payload_checksum=True)
    decoded, _ = decode_frame(raw)
    assert decoded.opcode is Opcode.QUERY


def test_partial_frame_returns_none() -> None:
    raw = encode_frame(make_frame())
    frame, consumed = try_decode_frame(raw[: HEADER_SIZE - 1])
    assert frame is None and consumed == 0
    frame, consumed = try_decode_frame(raw[:-1])
    assert frame is None and consumed == 0


def test_invalid_magic() -> None:
    raw = b"XXXX" + encode_frame(make_frame())[4:]
    with pytest.raises(AuraProtocolError):
        decode_frame(raw)


def test_header_checksum_mismatch() -> None:
    raw = bytearray(encode_frame(make_frame()))
    raw[5] ^= 0xFF  # corrupt the opcode byte without fixing the header CRC
    with pytest.raises(AuraProtocolError):
        decode_frame(bytes(raw))


def test_payload_checksum_mismatch() -> None:
    raw = bytearray(encode_frame(make_frame(), payload_checksum=True))
    raw[-1] ^= 0xFF  # corrupt the trailing payload checksum
    with pytest.raises(AuraProtocolError):
        decode_frame(bytes(raw))


def test_version_mismatch() -> None:
    raw = bytearray(encode_frame(make_frame()))
    raw[4] = 99  # bogus version
    # fix header CRC so we reach the version check
    import struct
    import zlib

    crc = zlib.crc32(bytes(raw[:28])) & 0xFFFFFFFF
    raw[28:32] = struct.pack(">I", crc)
    with pytest.raises(AuraProtocolVersionError):
        decode_frame(bytes(raw))


def test_payload_too_large_on_encode() -> None:
    with pytest.raises(AuraProtocolError):
        encode_frame(Frame(opcode=Opcode.PING, payload=b"x" * 1000), max_payload_bytes=100)


def test_payload_too_large_on_decode() -> None:
    raw = encode_frame(Frame(opcode=Opcode.PING, payload=b"x" * 500))
    with pytest.raises(AuraProtocolError):
        decode_frame(raw, max_payload_bytes=100)


def test_magic_constant() -> None:
    raw = encode_frame(make_frame())
    assert raw[:4] == MAGIC


def test_two_frames_in_buffer() -> None:
    a = encode_frame(Frame(opcode=Opcode.PING, payload=b"a", request_id=1))
    b = encode_frame(Frame(opcode=Opcode.PONG, payload=b"b", request_id=2))
    buffer = a + b
    f1, c1 = decode_frame(buffer)
    f2, c2 = decode_frame(buffer[c1:])
    assert f1.request_id == 1
    assert f2.request_id == 2
    assert c1 + c2 == len(buffer)


def _refresh_header_crc(raw: bytearray) -> bytearray:
    """Recompute the 28-byte header CRC so a decoder reaches post-CRC validation."""
    import struct
    import zlib

    crc = zlib.crc32(bytes(raw[:28])) & 0xFFFFFFFF
    raw[28:32] = struct.pack(">I", crc)
    return raw


def test_unknown_opcode_rejected() -> None:
    raw = bytearray(encode_frame(make_frame()))
    raw[5] = 200  # an opcode value not in the Opcode enum
    _refresh_header_crc(raw)
    with pytest.raises(AuraProtocolError, match="Unknown opcode"):
        decode_frame(bytes(raw))


def test_invalid_compression_marker_rejected() -> None:
    raw = bytearray(encode_frame(make_frame()))
    raw[7] = 77  # not a valid Compression value
    _refresh_header_crc(raw)
    with pytest.raises(AuraProtocolError, match="compression marker"):
        decode_frame(bytes(raw))


def test_corrupt_compressed_payload_rejected() -> None:
    raw = bytearray(encode_frame(make_frame(), compress=True))
    # Corrupt a byte inside the (compressed) body so zlib.decompress fails. The body
    # starts right after the 32-byte header.
    raw[HEADER_SIZE + 2] ^= 0xFF
    _refresh_header_crc(raw)
    with pytest.raises(AuraProtocolError, match="decompress"):
        decode_frame(bytes(raw))
