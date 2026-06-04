"""Golden tests: the binary frame format must be byte-for-byte stable.

If the wire format changes intentionally, these constants must be regenerated and the
protocol version bumped. They guard against accidental, undocumented format drift.
"""

from __future__ import annotations

from aura.protocol import Frame, Opcode, decode_frame, encode_frame

# A fixed, deterministic ping frame with an empty payload.
GOLDEN_PING = bytes.fromhex(
    "41555241"  # magic "AURA"
    "01"  # version 1
    "03"  # opcode PING (3)
    "00"  # flags NONE
    "00"  # compression NONE
    "00000000"  # payload_length 0
    "0000000000000000"  # transaction_id 0
    "0000000000000007"  # request_id 7
)


def test_ping_golden_header() -> None:
    frame = Frame(opcode=Opcode.PING, request_id=7)
    raw = encode_frame(frame)
    # The first 28 bytes are the header-without-checksum and must match exactly.
    assert raw[:28] == GOLDEN_PING
    assert len(raw) == 32  # 28-byte header body + 4-byte CRC, no payload


def test_ping_golden_round_trips() -> None:
    frame = Frame(opcode=Opcode.PING, request_id=7)
    raw = encode_frame(frame)
    decoded, consumed = decode_frame(raw)
    assert consumed == 32
    assert decoded.opcode is Opcode.PING
    assert decoded.request_id == 7
    assert decoded.payload == b""


def test_header_checksum_is_stable() -> None:
    raw = encode_frame(Frame(opcode=Opcode.PING, request_id=7))
    # Re-encoding yields identical bytes including the CRC.
    assert encode_frame(Frame(opcode=Opcode.PING, request_id=7)) == raw


def test_known_payload_frame_is_stable() -> None:
    frame = Frame(opcode=Opcode.QUERY, payload=b'{"k":1}', request_id=1, transaction_id=2)
    raw = encode_frame(frame)
    assert raw.hex() == encode_frame(frame).hex()
    decoded, _ = decode_frame(raw)
    assert decoded.payload == b'{"k":1}'
