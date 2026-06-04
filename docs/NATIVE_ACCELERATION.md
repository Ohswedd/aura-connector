# Native acceleration

Aura Connector runs entirely in pure Python by default. The pure-Python implementation is
the complete, correct, benchmarked baseline, and it is always available with no compiler
and no Rust toolchain.

An optional native extension can accelerate two byte-exact hot paths. It is shipped as an
in-repository extra, not a separate package, and it never changes the public API or any
observable behaviour.

## What the native path covers

A single adapter, `aura._native`, sits in front of two boundaries and dispatches to the
compiled `aura_native` extension (`crates/aura_native`) when it is installed, or to an
identical pure-Python reference otherwise:

- Protocol frame checksums: the CRC32 over the frame header and optional payload, used by
  `protocol/frames.py` and `protocol/codec.py`.
- Vector packing: fixed-dimension validation and `Vector.pack()` / `Vector.from_bytes()`
  over little-endian f32, used by `vectors.py`.

Both paths are covered by parity tests so that the native and pure-Python implementations
produce identical bytes.

## Design contract

- The pure-Python implementations are the reference behaviour and are always present.
- If `aura_native` imports successfully, selected operations dispatch to it transparently.
- The public API is identical with or without the extension.
- `AURA_DISABLE_NATIVE=1` forces the pure-Python path, which is useful for debugging and
  for measuring the two paths against each other.

Check the active status at runtime:

```python
from aura import native_status

print(native_status())
```

or from the command line:

```bash
aura doctor
```

## Building the extension locally

The extension is built with [maturin](https://www.maturin.rs/) and a stable Rust
toolchain. Install the tooling extra and build into your active environment:

```bash
python -m pip install -e ".[dev,native]"
maturin develop --manifest-path crates/aura_native/Cargo.toml --release
aura doctor   # native_acceleration: available
```

For CI and reproducible installs, prefer building a wheel and installing it:

```bash
maturin build --manifest-path crates/aura_native/Cargo.toml --release
python -m pip install crates/aura_native/target/wheels/*.whl
```

## Honest performance notes

Native code is not automatically faster. CPython's standard library is already highly
optimized for some of these operations.

- Vector packing benefits from the native path on typical inputs.
- CRC32 through the native path can be slower than Python's optimized zlib routine on some
  inputs, because zlib's CRC32 is a tuned C implementation already.

No zero-copy behaviour is claimed. Pure Python cannot guarantee zero-copy here, and the
native path does not change that contract. Run the benchmark on your own hardware before
drawing conclusions:

```bash
python benchmarks/bench_native_acceleration.py
```

The benchmark prints measured timings for both paths and contains no precomputed numbers.
