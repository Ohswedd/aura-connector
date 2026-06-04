"""Benchmark: protocol frame encode/decode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _bench import measure
from aura.protocol import Frame, Opcode, decode_frame, encode_frame
from aura.protocol.messages import QueryRequest, encode_body

_BODY = encode_body(
    QueryRequest(ir={"operation": "select", "model": "User", "filters": []}).to_payload()
)
_FRAME = Frame(opcode=Opcode.QUERY, payload=_BODY, request_id=1, transaction_id=0)
_ENCODED = encode_frame(_FRAME, payload_checksum=True)


def encode_simple() -> None:
    encode_frame(_FRAME, payload_checksum=True)


def decode_simple() -> None:
    decode_frame(_ENCODED)


if __name__ == "__main__":
    print("Protocol benchmark (real measured timings):")
    measure("encode_point_read_frame", encode_simple)
    measure("decode_point_read_frame", decode_simple)
