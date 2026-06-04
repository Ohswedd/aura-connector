<div align="center">

# Aura Connector

**Async-native, type-safe Python client and connector for AuraDB.**

[![CI](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml)
[![Native](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml)
[![PyPI](https://img.shields.io/pypi/v/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![Python](https://img.shields.io/pypi/pyversions/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

</div>

Aura Connector gives Python teams ORM-level productivity with driver-level control.
Declare typed models once, compose queries with a fluent, injection-safe builder, and
execute relational, document, vector, hybrid, and graph workloads through a single async
client over a deterministic binary wire protocol.

It is useful today: the package ships an in-memory reference server and a complete
pure-Python core, so the examples and the full test suite run with no external database
and no compiler.

```python
import asyncio
from aura import Aura, Model, Field, Vector


class Workspace(Model):
    id: int = Field(primary_key=True)
    name: str


class Document(Model):
    id: int = Field(primary_key=True)
    workspace: Workspace = Field(link=True)
    title: str
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect(
        "aura+memory://localhost/app", models=[Workspace, Document]
    ) as client:
        ws = await client.insert(Workspace(id=1, name="Acme"))
        await client.bulk_insert(
            Document,
            [
                Document(id=1, workspace=ws, title="Refunds", body="...", embedding=[1, 0, 0]),
                Document(id=2, workspace=ws, title="Billing", body="...", embedding=[0, 1, 0]),
            ],
        )

        # Typed, injection-safe query building.
        docs = await (
            client.query(Document)
            .where(Document.title.contains("Refund"))
            .order_by(Document.id.desc())
            .limit(10)
            .all()
        )

        # Vector nearest-neighbour search.
        matches = await (
            client.search(Document)
            .nearest(Document.embedding, [1, 0, 0], metric="cosine")
            .limit(5)
            .all()
        )
        print(docs, matches)


asyncio.run(main())
```

## Why Aura Connector matters

Python data access usually forces a trade. ORMs give you typed models and ergonomics but
hide the wire and leak lazy queries. Raw drivers give you control but hand back untyped
tuples and string SQL. Vector search lives in yet another SDK with its own client. Aura
Connector unifies these behind one typed, async client:

- **One typed client** for relational, document, vector, hybrid, and graph access.
- **Async-first.** `asyncio` is the primary execution model, not a wrapper.
- **Explicit relationship loading.** No hidden lazy network IO. `.include()` is required,
  and accessing an unloaded relationship raises a clear error.
- **Injection-safe by construction.** Queries compile to an immutable AST and Query IR,
  never to concatenated strings.
- **Deterministic binary protocol.** Frames are golden-tested, length-bounded, and
  checksummed.
- **Honest performance.** A complete pure-Python core with reproducible benchmarks, plus
  optional in-repository native acceleration with a fallback that is always available.

## Problems it solves

- Replaces three separate libraries (ORM, driver, vector SDK) with one typed surface.
- Removes the N+1 surprise by making relationship loading explicit.
- Keeps queries safe without forcing you to hand-write parameter binding.
- Makes schema changes reviewable: diff two model versions into a migration plan and gate
  destructive changes in CI before anything runs.
- Gives you metrics, latency percentiles, and secret-free query fingerprints in-process,
  with an optional OpenTelemetry bridge.

## How it compares

| Tool | Typed models | Async-native | Injection-safe builder | Vector fields | Wire protocol control |
|---|---|---|---|---|---|
| SQLAlchemy | yes | partial | yes | via extensions | abstracted |
| Django ORM | yes | partial | yes | via extensions | abstracted |
| psycopg / asyncpg | no | yes (asyncpg) | manual binding | no | low level |
| Prisma-style clients | yes | yes | yes | partial | abstracted |
| Vector database SDKs | partial | varies | no | yes | per vendor |
| **Aura Connector** | yes | yes | yes | first-class | deterministic, documented |

The comparison describes design focus, not a benchmark. Each tool above is excellent at
its own goals.

## What Aura Connector is and is not

It **is** a production-grade client and connector: model system, query builder, binary
protocol, transports, hydration, migrations tooling, observability, and a CLI, with a
pure-Python core and optional native acceleration.

It is **not** a database server. AuraDB server features (storage, distributed
transactions, server-side cost-based planning, lock and impact estimation on a live
cluster) live in a separate project and are not implemented or claimed here. See
[AuraDB boundary](docs/AURADB_BOUNDARY.md).

## Installation

```bash
pip install aura-connector                 # pure-Python, always works, no compiler
pip install "aura-connector[native]"       # + optional native acceleration tooling
```

The import package name is `aura`:

```python
import aura
```

Development install from a clone:

```bash
python -m pip install -e ".[dev]"
```

Requires Python 3.11 or newer. The pure-Python path needs no compiler. Optional native
acceleration (CRC32 plus f32 vector packing) lives in this repository under
`crates/aura_native` and is built with maturin (requires a Rust toolchain):

```bash
maturin develop --manifest-path crates/aura_native/Cargo.toml --release
aura doctor   # native_acceleration: available
```

See [Native acceleration](docs/NATIVE_ACCELERATION.md).

## Quick start

Aura uses `aura://` DSNs:

| Scheme | Meaning |
|---|---|
| `aura://host:port/db` | Plaintext TCP |
| `auras://host:port/db` | TLS TCP |
| `aura+tcp://...` | Explicit TCP |
| `aura+memory://...` | In-memory reference server (tests, examples, local dev) |

The in-memory reference server is a real implementation of the protocol and a query
engine, so no live AuraDB cluster is required to run the examples or the test suite.

```python
async with Aura.connect("aura+memory://localhost/app", models=[User]) as client:
    await client.ping()
```

## Model example

```python
from aura import Model, Field, Vector


class User(Model):
    id: int = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    display_name: str | None = Field(default=None)
    embedding: Vector[512] | None = None
```

## Query example

```python
cheap = await (
    client.query(Product)
    .where(Product.price_cents < 5000)
    .order_by(Product.price_cents.asc())
    .all()
)

await client.update(Product).where(Product.id == 2).set(price_cents=2499).execute()

total = await client.query(Product).count()
exists = await client.query(Product).where(Product.id == 3).exists()
```

## Vector example

```python
matches = await (
    client.search(Document)
    .nearest(Document.embedding, query_vector, metric="cosine")
    .limit(10)
    .all()
)
```

Supported metrics are `cosine`, `euclidean`, and `dot`. See [Vectors](docs/VECTORS.md).

## Migration example

```python
from aura import diff_schemas, generate_migration, schema_document

plan = generate_migration(old_models, new_models)
print(plan.format())
plan.require_safe()   # raises AuraMigrationError if the plan is destructive
```

The same gate is available in CI through `aura migrate diff --require-safe`. See
[Migrations](docs/MIGRATIONS.md).

## Observability example

```python
async with Client.connect(
    "aura+memory://localhost/shop",
    models=[Product],
    telemetry={"opentelemetry": True, "capture_query_ir": "redacted"},
) as client:
    await client.query(Product).filter(Product.price_cents > 500).all()
    snapshot = client.metrics.snapshot()   # counts, latency p50/p95/p99, bytes, retries
```

Query fingerprints are stable across bound values and never contain the values
themselves. See [Observability](docs/OBSERVABILITY.md).

## CLI example

The package installs an `aura` console script for offline, server-independent tasks:

```bash
aura version
aura doctor                                            # environment, deps, native status
aura schema compile --app myapp.models --out schema.json
aura migrate diff --from old.json --to myapp.models --require-safe
aura explain file query-ir.json                        # client-side static plan
aura bench local --iterations 20000                    # in-process micro-benchmarks
aura generate stubs --models myapp/models.py           # faithful .pyi from models
```

See [CLI](docs/CLI.md).

## Native acceleration

The pure-Python core is the complete, benchmarked baseline and is always available. An
optional in-repository Rust/PyO3 extension accelerates two byte-exact hot paths (frame
CRC32 and fixed-dimension f32 vector packing) when built and installed. The public API and
all observable behaviour are identical with or without it, parity is enforced by tests,
and `AURA_DISABLE_NATIVE=1` forces the pure-Python path.

No zero-copy behaviour is claimed, and the native path is not always faster: in the
included benchmark, CRC32 through the native path can be slower than Python's optimized
zlib routine on some inputs, while vector packing is faster. Measure on your own hardware.
See [Native acceleration](docs/NATIVE_ACCELERATION.md).

## AuraDB boundary

This package is the client and connector. AuraDB server features are a separate future
project. Operations that require a live cluster (applying migrations, server-side cost
estimation, distributed transaction coordination) fail with a structured error rather than
pretending to succeed. See [AuraDB boundary](docs/AURADB_BOUNDARY.md).

## Documentation

- [Getting Started](docs/GETTING_STARTED.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Models](docs/MODELS.md) and [Vectors](docs/VECTORS.md)
- [Query Builder](docs/QUERY_BUILDER.md)
- [Client](docs/CLIENT.md)
- [Protocol](docs/PROTOCOL.md) and [Transports](docs/TRANSPORTS.md)
- [Observability](docs/OBSERVABILITY.md) and [Migrations](docs/MIGRATIONS.md)
- [Errors](docs/ERRORS.md) and [Testing](docs/TESTING.md)
- [Native Acceleration](docs/NATIVE_ACCELERATION.md) and [AuraDB Boundary](docs/AURADB_BOUNDARY.md)

## Benchmarks

The `benchmarks/` directory contains real, in-process micro-benchmarks for protocol
encode/decode, model hydration, Query IR construction, vector serialization, an
end-to-end round trip through the reference transport, and pure-Python versus native
acceleration. They print measured timings on your machine:

```bash
python benchmarks/bench_protocol.py
python benchmarks/bench_native_acceleration.py
```

Numbers vary by hardware. The benchmarks contain no precomputed or fabricated results.

## Testing

```bash
python -m pytest -vv
```

The suite is deterministic and requires no external services. It covers models, fields,
vectors, schema generation, the query builder, protocol frames and golden bytes,
transports, client lifecycle, hydration, errors, migrations, observability, typing, and
example smoke tests. See [Testing](docs/TESTING.md).

## Security

The client validates and length-bounds every protocol frame, fails closed on protocol
errors, never embeds secrets in errors or logs, and redacts credentials in configuration
`repr`. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the dev and
native build, test commands, and code style. Please also read our
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
