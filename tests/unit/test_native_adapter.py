"""Tests for the native acceleration adapter (:mod:`aura._native`).

These tests exercise the *adapter contract* — the boolean availability flag,
backend name, the env-var fallback override, and the pure-Python reference
behaviour — independently of whether the compiled ``aura_native`` extension is
present. The cross-implementation parity checks live in
``tests/native/test_native_parity.py``.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from aura import _native
from aura.errors import AuraValidationError
from aura.vectors import Vector


def test_native_available_is_bool() -> None:
    assert isinstance(_native.NATIVE_AVAILABLE, bool)


def test_backend_name_consistent_with_availability() -> None:
    name = _native.native_backend_name()
    assert name in {"aura_native", "pure-python"}
    assert (name == "aura_native") == _native.NATIVE_AVAILABLE


def test_env_disable_forces_pure_python(monkeypatch: pytest.MonkeyPatch) -> None:
    # The module-level flag is computed at import; verify the predicate the module
    # uses honours the documented override values.
    monkeypatch.setenv("AURA_DISABLE_NATIVE", "1")
    assert _native._env_disabled() is True
    monkeypatch.setenv("AURA_DISABLE_NATIVE", "0")
    assert _native._env_disabled() is False
    monkeypatch.delenv("AURA_DISABLE_NATIVE", raising=False)
    assert _native._env_disabled() is False


def test_crc32_matches_zlib() -> None:
    for data in (b"", b"a", b"AURA", bytes(range(256)) * 4):
        assert _native.crc32(data) == zlib.crc32(data) & 0xFFFFFFFF


def test_crc32_pure_python_matches_dispatch() -> None:
    data = b"the quick brown fox" * 17
    assert _native._py_crc32(data) == _native.crc32(data)


def test_validate_vector_accepts_valid() -> None:
    assert _native.validate_vector([1, 2, 3], 3) == [1.0, 2.0, 3.0]


def test_validate_vector_rejects_wrong_dimension() -> None:
    with pytest.raises(AuraValidationError):
        _native.validate_vector([1.0, 2.0], 3)


def test_validate_vector_rejects_non_numeric() -> None:
    with pytest.raises(AuraValidationError):
        _native.validate_vector(["not-a-number", 2.0], 2)


def test_validate_vector_rejects_non_finite() -> None:
    with pytest.raises(AuraValidationError):
        _native.validate_vector([float("inf"), 1.0], 2)


def test_validate_vector_rejects_bad_dimension() -> None:
    with pytest.raises(AuraValidationError):
        _native.validate_vector([1.0], 0)


def test_pack_unpack_roundtrip_at_f32_precision() -> None:
    values = [0.0, 1.0, -1.5, 2.25, 100.0]
    packed = _native.pack_f32_vector(values, len(values))
    assert isinstance(packed, bytes)
    assert len(packed) == 4 * len(values)
    # These values are all exactly representable in f32, so the round-trip is exact.
    assert _native.unpack_f32_vector(packed, len(values)) == values


def test_pack_matches_struct_little_endian_f32() -> None:
    values = [0.5, 0.25, 0.125]
    expected = struct.pack("<3f", *values)
    assert _native.pack_f32_vector(values, 3) == expected


def test_unpack_rejects_wrong_length() -> None:
    with pytest.raises(AuraValidationError):
        _native.unpack_f32_vector(b"\x00\x00\x00", 3)  # 3 bytes, not 12


def test_vector_pack_uses_adapter_roundtrip() -> None:
    vec = Vector[4]([1.0, 2.0, 3.0, 4.0])
    packed = vec.pack()
    assert len(packed) == 16
    restored = Vector.from_bytes(packed, 4)
    assert restored == vec
    assert isinstance(restored, Vector)
