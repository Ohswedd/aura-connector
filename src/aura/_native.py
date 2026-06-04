"""Optional native acceleration adapter with a guaranteed pure-Python fallback.

This module is the single boundary between the connector's hot paths (protocol
checksums and fixed-dimension vector packing) and the optional compiled extension
``aura_native`` (a Rust/PyO3 crate that lives in this repository under
``crates/aura_native``; see ``docs/NATIVE_ACCELERATION.md``).

Design contract:

* The pure-Python implementations in this module are the *reference* behaviour and
  are always available. They never raise unless the input is genuinely invalid.
* If the compiled ``aura_native`` extension imports successfully, the public
  functions dispatch to it; otherwise they use the pure-Python path. Native and
  pure-Python outputs are byte-for-byte identical (verified by the parity tests).
* Setting ``AURA_DISABLE_NATIVE`` to a truthy value forces the pure-Python path,
  which is used both operationally and by the test suite to exercise fallback.
* The public API of the connector does not change based on whether native is
  present. Native is a transparent speed path, never a behavioural one.

Vector byte format (shared by native and pure-Python): each component is an
IEEE-754 32-bit float encoded little-endian, contiguous, with no header. A vector
of dimension ``N`` therefore occupies exactly ``4 * N`` bytes.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from collections.abc import Sequence

from .errors import AuraValidationError

__all__ = [
    "NATIVE_AVAILABLE",
    "native_backend_name",
    "native_status",
    "crc32",
    "validate_vector",
    "pack_f32_vector",
    "unpack_f32_vector",
]


def _env_disabled() -> bool:
    raw = os.environ.get("AURA_DISABLE_NATIVE", "")
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


_DISABLED_BY_ENV = _env_disabled()

#: The imported native module, or ``None`` when unavailable/disabled.
_backend: object | None = None
_backend_name = "pure-python"

if not _DISABLED_BY_ENV:
    try:
        import aura_native as _imported

        _backend = _imported
        _backend_name = "aura_native"
    except ImportError:
        _backend = None
        _backend_name = "pure-python"

#: ``True`` when the compiled extension is loaded and in use.
NATIVE_AVAILABLE: bool = _backend is not None


def native_backend_name() -> str:
    """Return the active backend name: ``"aura_native"`` or ``"pure-python"``."""
    return _backend_name


def native_status() -> dict[str, object]:
    """Return a diagnostic snapshot of the native acceleration backend.

    Keys: ``available`` (bool), ``backend`` (str), ``disabled_by_env`` (bool),
    ``version`` (the extension's version string, or ``None`` in pure-Python mode).
    This is the data ``aura doctor`` and :func:`aura.native_status` surface; it never
    raises and performs no I/O.
    """
    version: str | None = None
    if _backend is not None:
        version = getattr(_backend, "__version__", None)
    return {
        "available": NATIVE_AVAILABLE,
        "backend": _backend_name,
        "disabled_by_env": _DISABLED_BY_ENV,
        "version": version,
    }


# --------------------------------------------------------------------------- #
# Pure-Python reference implementations
# --------------------------------------------------------------------------- #
def _py_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _check_dimension(dimension: int) -> None:
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        raise AuraValidationError(
            f"Vector dimension must be a positive int, got {dimension!r}",
            context={"dimension": dimension},
        )


def _py_validate_vector(values: Sequence[float], dimension: int) -> list[float]:
    _check_dimension(dimension)
    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AuraValidationError(
                f"Vector components must be numeric, got {type(value).__name__}"
            ) from exc
        if not math.isfinite(number):
            raise AuraValidationError("Vector components must be finite numbers")
        out.append(number)
    if len(out) != dimension:
        raise AuraValidationError(
            f"Vector expected dimension {dimension}, got {len(out)}",
            context={"expected": dimension, "actual": len(out)},
        )
    return out


def _py_pack_f32_vector(values: Sequence[float], dimension: int) -> bytes:
    validated = _py_validate_vector(values, dimension)
    return struct.pack(f"<{dimension}f", *validated)


def _py_unpack_f32_vector(data: bytes, dimension: int) -> list[float]:
    _check_dimension(dimension)
    expected = dimension * 4
    if len(data) != expected:
        raise AuraValidationError(
            f"Packed vector expected {expected} bytes, got {len(data)}",
            context={"expected": expected, "actual": len(data)},
        )
    return list(struct.unpack(f"<{dimension}f", data))


# --------------------------------------------------------------------------- #
# Public dispatch surface (native when available, pure-Python otherwise)
# --------------------------------------------------------------------------- #
def crc32(data: bytes) -> int:
    """CRC32 of ``data`` as an unsigned 32-bit integer (matches :func:`zlib.crc32`)."""
    if _backend is not None:
        return int(_backend.native_crc32(data))  # type: ignore[attr-defined]
    return _py_crc32(data)


def validate_vector(values: Sequence[float], dimension: int) -> list[float]:
    """Validate ``values`` as a ``dimension``-length finite float vector.

    Returns a fresh ``list[float]``. Raises :class:`AuraValidationError` on a wrong
    dimension or a non-finite/non-numeric component. The native backend's own errors
    are normalised to :class:`AuraValidationError` so behaviour is identical.
    """
    if _backend is not None:
        try:
            return list(_backend.native_validate_vector(list(values), dimension))  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            # PyO3 raises TypeError when a component cannot coerce to a real number;
            # the pure-Python path treats that as a non-numeric component. Normalise
            # both to AuraValidationError so behaviour is identical across backends.
            raise AuraValidationError(str(exc)) from exc
    return _py_validate_vector(values, dimension)


def pack_f32_vector(values: Sequence[float], dimension: int) -> bytes:
    """Validate then pack ``values`` into ``4 * dimension`` little-endian f32 bytes."""
    if _backend is not None:
        try:
            return bytes(_backend.native_pack_f32_vector(list(values), dimension))  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            raise AuraValidationError(str(exc)) from exc
    return _py_pack_f32_vector(values, dimension)


def unpack_f32_vector(data: bytes, dimension: int) -> list[float]:
    """Unpack ``4 * dimension`` little-endian f32 bytes into a ``list[float]``."""
    if _backend is not None:
        try:
            return list(_backend.native_unpack_f32_vector(bytes(data), dimension))  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            raise AuraValidationError(str(exc)) from exc
    return _py_unpack_f32_vector(data, dimension)
