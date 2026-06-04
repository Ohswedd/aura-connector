"""Benchmark: pure-Python vs optional native acceleration.

Compares the pure-Python reference implementations in :mod:`aura._native` against
the dispatched public functions (which use the compiled ``aura_native`` extension
when it is installed). When native is unavailable the script prints a clear notice
and still runs the pure-Python baseline. No speedup numbers are fabricated: the
script only reports timings it actually measured on this machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _bench import measure
from aura import _native

_DATA = bytes(range(256)) * 256  # 64 KiB
_RAW = [float(i) / 1536 for i in range(1536)]
_PACKED = _native._py_pack_f32_vector(_RAW, 1536)


def py_crc32() -> None:
    _native._py_crc32(_DATA)


def dispatch_crc32() -> None:
    _native.crc32(_DATA)


def py_validate() -> None:
    _native._py_validate_vector(_RAW, 1536)


def dispatch_validate() -> None:
    _native.validate_vector(_RAW, 1536)


def py_pack() -> None:
    _native._py_pack_f32_vector(_RAW, 1536)


def dispatch_pack() -> None:
    _native.pack_f32_vector(_RAW, 1536)


def py_unpack() -> None:
    _native._py_unpack_f32_vector(_PACKED, 1536)


def dispatch_unpack() -> None:
    _native.unpack_f32_vector(_PACKED, 1536)


if __name__ == "__main__":
    backend = _native.native_backend_name()
    print(f"Native acceleration benchmark (backend in use: {backend})")
    if not _native.NATIVE_AVAILABLE:
        print(
            "  Native extension not installed/enabled — measuring the pure-Python\n"
            "  baseline only. Build it with: maturin develop --manifest-path\n"
            "  crates/aura_native/Cargo.toml --release  (requires a Rust toolchain),\n"
            "  then re-run to compare. The 'dispatch_*' rows below equal 'py_*'."
        )
    print()
    print("CRC32 over 64 KiB:")
    measure("py_crc32", py_crc32, iterations=5000)
    measure("dispatch_crc32", dispatch_crc32, iterations=5000)
    print("Vector validation (1536-dim):")
    measure("py_validate", py_validate, iterations=5000)
    measure("dispatch_validate", dispatch_validate, iterations=5000)
    print("Vector pack f32 (1536-dim):")
    measure("py_pack", py_pack, iterations=5000)
    measure("dispatch_pack", dispatch_pack, iterations=5000)
    print("Vector unpack f32 (1536-dim):")
    measure("py_unpack", py_unpack, iterations=5000)
    measure("dispatch_unpack", dispatch_unpack, iterations=5000)
