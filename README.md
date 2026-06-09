<div align="center">

# Aura Connector

**A typed async Python connector and query layer for AuraDB.**

[![CI](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/ci.yml)
[![Native](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml/badge.svg)](https://github.com/Ohswedd/aura-connector/actions/workflows/native.yml)
[![PyPI](https://img.shields.io/pypi/v/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![Python](https://img.shields.io/pypi/pyversions/aura-connector.svg)](https://pypi.org/project/aura-connector/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

</div>

Aura Connector is an async, typed Python client for [**AuraDB**](https://github.com/Ohswedd/auradb)
and a uniform query layer over the databases you already run. Declare typed models once,
compose injection-safe queries with a fluent builder, and run them against AuraDB or another
backend by changing only the DSN. AuraDB is the native, high-performance target over the Aura
Wire Protocol; the other backends make the same model and query API useful immediately on
existing infrastructure.

**Aura Connector v0.6.0 is the matching client for AuraDB v1.2.0** (query ergonomics:
aggregations, terms facets, cooperative query timeouts), building on the v1.1.0 search and
ranking features (`search_text` BM25, `search_vector`, `search_hybrid`). The `.timeout(ms)`
query option is now enforced end-to-end by AuraDB v1.2.0. Feature differences between
backends are honest: search/ranking APIs require AuraDB capabilities, and a backend that does
not support a requested feature raises a structured capability error instead of pretending to
support it.

It is useful today with zero setup — the package ships an in-memory reference backend and
first-class SQLite, so the examples and the full default test suite run with no external
database and no compiler.

```python
import asyncio
from aura import Aura, Model, Field, Vector


class Document(Model):
    id: int = Field(primary_key=True)
    title: str
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/app", models=[Document]) as client:
        await client.insert(Document(id=1, title="Refunds", body="...", embedding=[1, 0, 0]))

        # Typed, injection-safe query building.
        docs = await (
            client.query(Document)
            .where(Document.title.contains("Refund"))
            .order_by(Document.id.desc())
            .limit(10)
            .all()
        )

        # Exact vector nearest-neighbour search.
        matches = await (
            client.search(Document)
            .nearest(Document.embedding, [1, 0, 0], metric="cosine")
            .limit(5)
            .all()
        )
        print(docs, matches)


asyncio.run(main())
```

## Why it matters

Python data access usually forces a trade: ORMs give typed models but hide the wire and leak
lazy queries; raw drivers give control but hand back untyped tuples and string SQL; vector
search lives in yet another SDK. Aura Connector unifies these behind one typed async client.

- **One typed client** for relational, document, vector, hybrid, and graph access.
- **Async-first.** `asyncio` is the primary execution model, not a wrapper.
- **Explicit relationship loading.** No hidden lazy network IO — `.include()` is required, and
  accessing an unloaded relationship raises a clear error.
- **Injection-safe by construction.** Queries compile to an immutable AST and Query IR, never
  to concatenated strings.
- **Honest about backends.** Unsupported features raise a structured error rather than being
  silently emulated.

## Install

The base install is dependency-free and includes the AuraDB protocol and in-memory backends.
Add an extra for each database backend you want:

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
the exact install command. Requires Python 3.11+; the pure-Python path needs no compiler. The
import package is `aura`. Optional native acceleration (CRC32 and f32 vector packing) is
built with maturin from `crates/aura_native` — see
[Native acceleration](docs/NATIVE_ACCELERATION.md).

## Compatibility

The connector reads the connected backend's advertised capabilities at handshake, so it
adapts to the server version rather than assuming one.

| Aura Connector | AuraDB server | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| **0.5.x** | **1.1.x** | AWP 1 | Recommended — first-class search and ranking (`search_text` BM25, `search_vector`, `search_hybrid`), typed scores, capability negotiation. |
| 0.5.x | 1.0.x | AWP 1 | Basic operations supported. Search APIs raise `AuraCapabilityError` because a pre-1.1.0 server does not advertise BM25/hybrid. |
| 0.4.x | 0.7.x / 1.0.x / 1.1.x | AWP 1 | Older line — CRUD, transactions, exact vector, and cluster-preview ergonomics; predates the search APIs. |
| 0.3.x | 0.2.x+ | AWP 1 | Native AuraDB backend (auth + TLS); no typed `not_leader` ergonomics. |
| 0.2.x | — | n/a | Not compatible with the authenticated, TLS-capable native backend. |

The search clauses are additive Query IR, so AWP 1 is unchanged across these rows. See
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Quick start

The fastest start needs no external service — SQLite (local) or the in-memory engine. Switch
to AuraDB or another database by changing only the DSN.

```python
import asyncio
from aura import Aura, Model, Field

class User(Model):
    id: int = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    display_name: str | None = Field(default=None)

async def main() -> None:
    async with Aura.connect("sqlite:///app.db", models=[User]) as db:
        await db.insert(User(id=1, email="ada@example.com", display_name="Ada"))
        ada = await db.User.find(id=1)
        print(ada.display_name)

asyncio.run(main())
```

The same code runs against other backends by changing only the DSN:

```python
async with Aura.connect("postgresql://user:pass@localhost:5432/app", models=[User]) as db: ...
async with Aura.connect("mongodb://localhost:27017/app", models=[User]) as db: ...
async with Aura.connect("auras://db.example.com:7171/app", models=[User]) as db: ...   # AuraDB over TLS
```

See [Getting started](docs/GETTING_STARTED.md), [Models](docs/MODELS.md), and the
[Query builder](docs/QUERY_BUILDER.md).

## AuraDB native backend

The `auradb://` (plaintext) and `auradbs://` (TLS) schemes connect to a running AuraDB server
over Aura Wire Protocol 1, including static-token authentication, TLS, and transactions with
read-your-writes. AuraDB v1.2.0 is the current coordinated server; the native backend speaks
AWP 1, which is frozen for the AuraDB v1 line.

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

A certificate the CA does not validate, or a hostname mismatch, fails the handshake — the
client never silently downgrades to plaintext. The legacy `aura://` / `memory://` schemes use
the connector's bundled reference protocol path, not the AuraDB network server. See
[docs/AURADB.md](docs/AURADB.md).

## Search and ranking

Against AuraDB v1.1.x and later (and the in-memory reference backend), the connector exposes
first-class ranked search. Exact vector search is the default and correctness baseline;
against AuraDB v1.2.0 the connector can opt a vector query into the approximate (HNSW)
preview (`search_vector(..., approximate=True)`) — not production ANN.

```python
from aura import search_scores

# Ranked full-text (BM25).
rows = await client.search(Doc).search_text("body", "vector index", rank="bm25").all()

# Exact vector search.
rows = await client.search(Doc).search_vector("embedding", q, metric="cosine", top_k=10).all()

# Hybrid text + vector, fused.
rows = await (
    client.search(Doc)
    .search_hybrid("body", "vector index", "embedding", q,
                   weights=(0.5, 0.5), fusion="weighted_sum", top_k=10)
    .all()
)

for row in rows:
    s = search_scores(row)
    print(s.rank, s.score, s.text_score, s.vector_score)
```

`.explain()` on any query or search builder returns the client-side Query IR plan; the
server-measured plan is available through AuraDB's `auradb search explain --analyze`.
**Search/ranking APIs require AuraDB capabilities.** The connector checks the connected
backend's advertised capabilities first; a backend that does not support a requested feature
raises `AuraCapabilityError` rather than emulating it. `client.capabilities()` is the
authoritative source for what the connected server supports. See
[docs/SEARCH_AND_RANKING.md](docs/SEARCH_AND_RANKING.md) and the
[`auradb_text_search`](examples/auradb_text_search.py),
[`auradb_hybrid_search`](examples/auradb_hybrid_search.py),
[`auradb_explain_analyze`](examples/auradb_explain_analyze.py), and
[`auradb_search_capabilities`](examples/auradb_search_capabilities.py) examples.

## Backends

The DSN scheme selects the backend — nothing else in your code changes:

| Scheme(s) | Backend | Extra |
|---|---|---|
| `auradb://`, `auradbs://` | **AuraDB server** (native AWP 1 over TCP/TLS) — auth + TLS | — |
| `aura://`, `auras://`, `aura+tcp://` | AuraDB reference protocol path (bundled; not the network server) | — |
| `aura+memory://`, `memory://` | In-memory reference engine | — |
| `sqlite://`, `sqlite+aiosqlite://` | SQLite (first-class local) | `[sqlite]` |
| `postgres://`, `postgresql://`, `postgresql+asyncpg://` | PostgreSQL | `[postgres]` |
| `mysql://`, `mariadb://`, `…+aiomysql://` | MySQL / MariaDB | `[mysql]` |
| `mongodb://`, `mongodb+motor://` | MongoDB (document-native) | `[mongodb]` |
| `redis://`, `redis+asyncio://` | Redis (limited key-value / cache) | `[redis]` |

| Capability | AuraDB | Memory | SQLite | PostgreSQL | MySQL | MongoDB | Redis |
|---|---|---|---|---|---|---|---|
| CRUD + filters | yes | yes | yes | yes | yes | yes | by key |
| Transactions | yes | yes | yes | yes | yes | no¹ | no |
| Relationships | yes | yes | yes | yes | yes | yes | no |
| JSON / document fields | yes | yes | yes² | yes² | yes² | yes | yes |
| Full-text search | yes | yes | no | yes | yes | no | no |
| Vector / hybrid search | yes | yes | no | no | no | no³ | no |
| Graph traversal | yes | yes | no | no | no | no | no |

¹ MongoDB transactions require a replica set. ² SQL backends store JSON/vector fields as JSON
text (the documented fallback). ³ MongoDB vector search requires a configured vector index.
Unsupported features raise `AuraCapabilityError` (alias `AuraBackendCapabilityError`) — never
silently emulated. Per-backend guides:
[AuraDB](docs/AURADB.md) · [SQLite](docs/SQLITE.md) · [PostgreSQL](docs/POSTGRESQL.md) ·
[MySQL](docs/MYSQL.md) · [MongoDB](docs/MONGODB.md) · [Redis](docs/REDIS.md). See
[docs/BACKENDS.md](docs/BACKENDS.md) and the
[capability matrix](docs/BACKEND_CAPABILITY_MATRIX.md).

## Error handling

Errors carry stable codes and non-sensitive context and never embed credentials. Two are
central to AuraDB use:

- **`AuraCapabilityError`** — a requested feature (e.g. BM25 or hybrid search) is not
  supported by the connected backend. The message names the backend and the missing
  capability so callers can branch on it.
- **`AuraNotLeaderError`** — in AuraDB's multi-node preview, a write reached a follower. It
  carries the leader-routing hints (`leader_addr`, `leader_node_id`, `current_node_id`,
  `retryable`, …). A `retryable=True` does **not** mean the connector retries automatically.

See the full taxonomy in [docs/ERRORS.md](docs/ERRORS.md).

## Transactions

The native backend supports `begin` / `commit` / `rollback` with read-your-writes; reads
inside a transaction observe its own staged writes and not its staged deletes, invisible to
other connections until commit. The model is **snapshot isolation**: read-your-writes over
committed state with optimistic (first-committer-wins) conflict detection on commit. It is
not serializable isolation, and the connector does not upgrade it. `transaction(isolation=…)`
defaults to `"snapshot"`; `"serializable"` is accepted only as a deprecated compatibility
alias for snapshot isolation.

In the cluster preview, transaction ids are node-local and a redirect mid-transaction is
**not** performed automatically: if a leader changes, restart the transaction against the new
leader. AuraDB's multi-node mode has no production high availability or automatic failover —
single-node is the recommended production deployment. See
[docs/TRANSACTIONS.md](docs/TRANSACTIONS.md) and the
[`auradb_not_leader`](examples/auradb_not_leader.py) /
[`auradb_leader_redirect`](examples/auradb_leader_redirect.py) examples.

## AuraDB boundary

This package is the client and connector. AuraDB server features (storage, server-side
cost-based planning, distributed transaction coordination on a live cluster) live in the
separate [AuraDB](https://github.com/Ohswedd/auradb) project. Operations that require a live
server fail with a structured error rather than pretending to succeed. See
[AuraDB boundary](docs/AURADB_BOUNDARY.md).

## Documentation

- [Getting Started](docs/GETTING_STARTED.md) · [Architecture](docs/ARCHITECTURE.md) ·
  [AuraDB boundary](docs/AURADB_BOUNDARY.md)
- [Models](docs/MODELS.md) · [Query Builder](docs/QUERY_BUILDER.md) ·
  [Client](docs/CLIENT.md) · [Vectors](docs/VECTORS.md)
- [Search & Ranking](docs/SEARCH_AND_RANKING.md) · [Transactions](docs/TRANSACTIONS.md) ·
  [Errors](docs/ERRORS.md) · [Migrations](docs/MIGRATIONS.md)
- [AuraDB backend](docs/AURADB.md) · [Backends](docs/BACKENDS.md) ·
  [Capability matrix](docs/BACKEND_CAPABILITY_MATRIX.md)
- [Protocol](docs/PROTOCOL.md) · [Transports](docs/TRANSPORTS.md) ·
  [SQL compiler](docs/SQL_COMPILER.md) · [Native acceleration](docs/NATIVE_ACCELERATION.md)
- [Observability](docs/OBSERVABILITY.md) · [CLI](docs/CLI.md) ·
  [Compatibility](docs/COMPATIBILITY.md) · [Testing](docs/TESTING.md) ·
  [Roadmap](docs/ROADMAP.md)

## Testing

```bash
python -m pytest -vv
```

The suite is deterministic and requires no external services. It covers models, fields,
vectors, schema generation, the query builder, protocol frames and golden bytes, transports,
client lifecycle, hydration, errors, migrations, observability, typing, and example smoke
tests. See [Testing](docs/TESTING.md).

## Security

The client validates and length-bounds every protocol frame, fails closed on protocol errors,
never embeds secrets in errors or logs, and redacts credentials in configuration `repr`. To
report a vulnerability, see [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the dev and
native build, test commands, and code style, and the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
