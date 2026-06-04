# Testing & Benchmarks

## Test suite

```bash
python -m pytest
```

The suite is deterministic and requires no external services, integration tests run
against the in-memory reference server (and a loopback TCP server). Categories:

- **Unit** (`tests/unit/`): errors, config/DSN, fields, vectors, models, schema, query
  expressions, query AST/IR, query builder, protocol codec, protocol golden frames,
  transport base, hydration, client lifecycle.
- **Integration** (`tests/integration/`): memory transport, protocol round-trip and
  transactions, query execution + hydration, client transactions/bulk/traversal, TCP
  transport over a real socket, and example smoke tests.
- **Typing** (`tests/typing/`): typed-usage sanity and `py.typed` presence.

`pytest-asyncio` is configured in `auto` mode (`pyproject.toml`), so `async def test_*`
functions run directly. Warnings are errors (`filterwarnings = ["error"]`).

## Validation gate

```bash
python -m pip install -e ".[dev,sqlite]"
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
python -m compileall src tests examples
python -m build
```

## Backend tests

The default suite runs with **no external services**. SQLite integration tests
(`tests/integration/test_sqlite_*.py`) run by default because SQLite is local; the SQL
compiler, dialect, schema, injection-safety, DSN-routing, capability, and driver-missing
checks are pure unit tests.

Tests for PostgreSQL, MySQL/MariaDB, MongoDB, and Redis are **optional and
environment-gated**: each skips cleanly unless its DSN env var is set. Stand the services up
with Docker and run them explicitly:

```bash
docker compose -f docker-compose.backends.yml up -d
export AURA_TEST_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/aura_test
export AURA_TEST_MYSQL_DSN=mysql://root:password@localhost:3306/aura_test
export AURA_TEST_MONGODB_DSN=mongodb://localhost:27017/aura_test
export AURA_TEST_REDIS_DSN=redis://localhost:6379/0
python -m pip install -e ".[dev,all-db]"
python -m pytest tests/integration -vv
docker compose -f docker-compose.backends.yml down -v
```

The `.github/workflows/databases.yml` workflow runs the same gated tests against service
containers in CI, separately from the lightweight main workflow.

## Benchmarks

Each script prints **real measured** timings for the local machine (never fabricated):

```bash
python benchmarks/bench_protocol.py
python benchmarks/bench_hydration.py
python benchmarks/bench_query_builder.py
python benchmarks/bench_vector_serialization.py
python benchmarks/bench_reference_roundtrip.py
python benchmarks/bench_native_acceleration.py
```

They cover protocol encode/decode, model hydration (vs. a naive baseline), Query IR
construction, vector construction/serialization, an end-to-end round trip through the
reference transport, and pure-Python vs. native acceleration. Numbers vary by hardware;
run the benchmark scripts locally to capture a sample on your machine.

## Native acceleration tests

`aura._native` provides an optional native (Rust/PyO3) speed path with a pure-Python
fallback. Two suites cover it (see `docs/NATIVE_ACCELERATION.md`):

- `tests/unit/test_native_adapter.py`, the adapter contract and pure-Python reference
  behaviour. Runs in every environment.
- `tests/native/test_native_parity.py`, byte-for-byte native vs. pure-Python parity.
  **Skipped with a clear reason** when the `aura_native` extension is not built; never
  failed.

Force the pure-Python fallback (also used to verify identical behaviour) with:

```bash
AURA_DISABLE_NATIVE=1 python -m pytest
AURA_DISABLE_NATIVE=1 aura doctor
```

To exercise the native path, build the extension first (requires a Rust toolchain):

```bash
python -m pip install -e ".[dev,native]"
maturin develop --manifest-path crates/aura_native/Cargo.toml --release
python -m pytest tests/native/test_native_parity.py -vv
```
