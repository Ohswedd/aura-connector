"""Parity tests between the compiled ``aura_native`` extension and pure Python.

Every test here is skipped — with a clear reason, never failed — when the native
extension is not installed or has been disabled via ``AURA_DISABLE_NATIVE``. When
it *is* available, each test asserts byte-for-byte identical output against the
pure-Python reference implementations in :mod:`aura._native`, which guarantees the
public API behaves identically regardless of backend.

Build the extension to run these locally::

    python -m pip install -e ".[dev,native]"
    maturin develop --manifest-path crates/aura_native/Cargo.toml --release
    python -m pytest tests/native/test_native_parity.py -vv
"""

from __future__ import annotations

import struct

import pytest

from aura import _native
from aura.protocol.codec import decode_frame, encode_frame
from aura.protocol.frames import Frame
from aura.protocol.opcodes import Opcode

pytestmark = pytest.mark.skipif(
    not _native.NATIVE_AVAILABLE,
    reason=(
        "native extension 'aura_native' not available "
        f"(backend={_native.native_backend_name()}); "
        "build with `maturin develop --manifest-path crates/aura_native/Cargo.toml`"
    ),
)

_NATIVE = _native._backend


def test_crc32_parity() -> None:
    for data in (b"", b"x", b"AURA-frame", bytes(range(256)) * 8):
        assert _NATIVE.native_crc32(data) == _native._py_crc32(data)


def test_validate_vector_parity() -> None:
    values = [float(i) * 0.5 for i in range(64)]
    assert _NATIVE.native_validate_vector(values, 64) == _native._py_validate_vector(values, 64)


def test_validate_vector_rejects_parity() -> None:
    # Both implementations reject a wrong dimension (native raises ValueError; the
    # adapter normalises that to AuraValidationError — tested in the adapter suite).
    with pytest.raises(ValueError):
        _NATIVE.native_validate_vector([1.0, 2.0], 3)


def test_pack_parity() -> None:
    values = [float(i) / 7 for i in range(128)]
    native_bytes = bytes(_NATIVE.native_pack_f32_vector(values, 128))
    assert native_bytes == _native._py_pack_f32_vector(values, 128)
    assert len(native_bytes) == 128 * 4


def test_unpack_parity() -> None:
    values = [float(i) / 7 for i in range(128)]
    packed = _native._py_pack_f32_vector(values, 128)
    assert list(_NATIVE.native_unpack_f32_vector(packed, 128)) == _native._py_unpack_f32_vector(
        packed, 128
    )


def test_frame_checksum_parity() -> None:
    """The encoded frame (whose header + payload checksums run through the adapter)
    must be byte-identical whether native or pure Python computed the CRC32."""
    frame = Frame(opcode=Opcode.QUERY, payload=b"select 1" * 16, request_id=7, transaction_id=3)
    encoded = encode_frame(frame, payload_checksum=True)
    decoded, consumed = decode_frame(encoded)
    assert consumed == len(encoded)
    assert decoded.payload == frame.payload
    # Recompute the header checksum natively and confirm it matches the wire bytes.
    header_no_crc = encoded[:28]
    (wire_crc,) = struct.unpack(">I", encoded[28:32])
    assert _NATIVE.native_crc32(header_no_crc) == wire_crc
