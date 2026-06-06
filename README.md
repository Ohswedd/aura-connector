<div align="center">

# Aura Connector

**A typed async data connector for AuraDB and existing databases.**

[![CI](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml)
[![Native](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml)
[![PyPI](https://img.shields.io/pypi/v/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![Python](https://img.shields.io/pypi/pyversions/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

</div>

Aura Connector is an async, typed Python data connector that gives teams **one model and
query API** across AuraDB, SQLite, PostgreSQL, MySQL/MariaDB, MongoDB, Redis, and an in-memory
reference backend. Declare typed models once, compose queries with a fluent, injection-safe
builder, and run them against whichever backend you choose by changing only the DSN.

[AuraDB](https://github.com/Ohswedd/auradb) remains the **native, high-performance backend**
with the strongest path through the Aura Wire Protocol; the backend adapters make Aura useful
immediately with infrastructure you already run. Feature differences between backends are
represented honestly through [backend capabilities](docs/BACKEND_CAPABILITY_MATRIX.md) —
unsupported features raise a structured error rather than being emulated.

It is useful today with zero setup: the package ships an in-memory reference server and
first-class SQLite support, so the examples and the full default test suite run with no
external database and no compiler.

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
cluster) live in the separate [AuraDB](https://github.com/Ohswedd/auradb) project and
are not implemented or claimed here. See [AuraDB boundary](docs/AURADB_BOUNDARY.md).

## Installation

The base install is dependency-free and includes the AuraDB protocol and in-memory backends.
Add an extra for each database backend you want to use:

```bash
pip install aura-connector                 # core: AuraDB protocol + in-memory backend
pip install "aura-connector[sqlite]"       # SQLite        (aiosqlite)
pip install "aura-connector[postgres]"     # PostgreSQL    (asyncpg)
pip install "aura-connector[mysql]"        # MySQL/MariaDB (aiomysql)
pip install "aura-connector[mongodb]"      # MongoDB       (motor)
pip install "aura-connector[redis]"        # Redis         (redis.asyncio)
pip install "aura-connector[sql]"          # SQLite + PostgreSQL + MySQL
pip install "aura-connector[all-db]"       # every database driver
pip install "aura-connector[native]"       # + optional native acceleration tooling
```

Selecting a backend whose driver is not installed raises `AuraDriverNotInstalledError` with
the exact install command. The import package name is `aura`:

```python
import aura
```

Development install from a clone:

```bash
python -m pip install -e ".[dev,sqlite]"
```

Requires Python 3.11 or newer. The pure-Python path needs no compiler. Optional native
acceleration (CRC32 plus f32 vector packing) lives in this repository under
`crates/aura_native` and is built with maturin (requires a Rust toolchain):

```bash
maturin develop --manifest-path crates/aura_native/Cargo.toml --release
aura doctor   # native_acceleration: available
```

See [Native acceleration](docs/NATIVE_ACCELERATION.md).

## Backends

The DSN scheme selects the backend — nothing else in your code changes:

| Scheme(s) | Backend | Extra |
|---|---|---|
| `auradb://`, `auradbs://` | **AuraDB v0.2.0 server** (native AWP 1 over TCP/TLS) — auth + TLS | — |
| `aura://`, `auras://`, `aura+tcp://` | AuraDB reference protocol path (bundled; not the v0.2.0 server) | — |
| `aura+memory://`, `memory://` | In-memory reference engine | — |
| `sqlite://`, `sqlite+aiosqlite://` | SQLite (first-class local) | `[sqlite]` |
| `postgres://`, `postgresql://`, `postgresql+asyncpg://` | PostgreSQL | `[postgres]` |
| `mysql://`, `mariadb://`, `…+aiomysql://` | MySQL / MariaDB | `[mysql]` |
| `mongodb://`, `mongodb+motor://` | MongoDB (document-native) | `[mongodb]` |
| `redis://`, `redis+asyncio://` | Redis (limited key-value / cache) | `[redis]` |

See [docs/BACKENDS.md](docs/BACKENDS.md) and the
[capability matrix](docs/BACKEND_CAPABILITY_MATRIX.md). Summary:

| Capability | AuraDB | Memory | SQLite | PostgreSQL | MySQL | MongoDB | Redis |
|---|---|---|---|---|---|---|---|
| CRUD + filters | yes | yes | yes | yes | yes | yes | by key |
| Transactions | yes | yes | yes | yes | yes | no¹ | no |
| Relationships | yes | yes | yes | yes | yes | yes | no |
| JSON / document fields | yes | yes | yes² | yes² | yes² | yes | yes |
| Vector / hybrid search | yes | yes | no | no | no | no³ | no |
| Graph traversal | yes | yes | no | no | no | no | no |

¹ MongoDB transactions require a replica set. ² SQL backends store JSON/vector fields as JSON
text (the documented fallback). ³ MongoDB vector search requires a configured vector index.
Unsupported features raise `AuraBackendCapabilityError` — they are never silently emulated.

### Native AuraDB backend (AuraDB v0.2.0)

The `auradb://` (plaintext) and `auradbs://` (TLS) schemes connect to a running **AuraDB
v0.2.0** server over the Aura Wire Protocol version 1, including static-token authentication,
TLS, and transactions with read-your-writes:

```python
from aura import connect
from aura.config import TokenAuth, TLSConfig

async with connect(
    "auradbs://db.example.com:7171/app",
    models=[User],
    auth=TokenAuth("my-secret-token"),
    tls=TLSConfig(enabled=True, ca_cert_path="/etc/aura/ca.pem"),
) as client:
    async with client.transaction() as tx:
        await tx.insert(User(id=1, email="ada@example.com"))
        # Read-your-writes: visible inside the transaction, not outside until commit.
        assert await tx.query(User).where(User.id == 1).count() == 1
```

**Compatibility:** Aura Connector 0.4.x talks to AuraDB 0.7.x over AWP 1 (and to AuraDB 0.6.x
single-node servers, where the cluster ergonomics simply never trigger). Aura Connector 0.2.x
does **not** speak the authenticated, TLS-capable native AWP path. The legacy `aura://`
schemes use the connector's bundled reference protocol path, not the AuraDB network server.
See [docs/AURADB.md](docs/AURADB.md).

**Cluster preview (experimental, opt-in):** AuraDB's multi-node mode has no production high
availability or automatic failover — single-node mode is the recommended production
deployment. When only the leader accepts writes, a write to a follower raises
`AuraNotLeaderError` carrying the leader address and redirect guidance. Catch it and call
`client.connect_to_leader(exc)`, or opt in to a bounded `client.with_leader_redirect()`. A
redirect preserves auth and TLS and refuses to silently drop TLS. See
[docs/AURADB.md](docs/AURADB.md), [docs/TRANSACTIONS.md](docs/TRANSACTIONS.md), and the
[`auradb_cluster`](examples/auradb_cluster.py) /
[`auradb_not_leader`](examples/auradb_not_leader.py) /
[`auradb_leader_redirect`](examples/auradb_leader_redirect.py) examples.

## Quick start

The fastest start needs no external service — SQLite (local) or the in-memory reference
engine. Switch to AuraDB or another database by changing only the DSN.

```python
import asyncio
from aura import Aura, Model, Field

class User(Model):
    id: int = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    display_name: str | None = Field(default=None)

async def main() -> None:
    # SQLite — first-class local backend, no server required.
    async with Aura.connect("sqlite:///app.db", models=[User]) as db:
        await db.insert(User(id=1, email="ada@example.com", display_name="Ada"))
        ada = await db.User.find(id=1)
        print(ada.display_name)

asyncio.run(main())
```

The same code runs against other backends by changing the DSN:

```python
# PostgreSQL
async with Aura.connect("postgresql://user:pass@localhost:5432/app", models=[User]) as db:
    users = await db.query(User).where(User.email.endswith("@example.com")).all()

# MongoDB (document-native)
async with Aura.connect("mongodb://localhost:27017/app", models=[User]) as db:
    await db.insert(User(id=2, email="grace@example.com"))

# In-memory reference engine (tests, examples, local dev)
async with Aura.connect("aura+memory://localhost/app", models=[User]) as db:
    await db.ping()

# AuraDB — the native, high-performance target (TLS)
async with Aura.connect("auras://cluster:7171/app", models=[User]) as db:
    await db.ping()
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
- [Roadmap](docs/ROADMAP.md)

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
