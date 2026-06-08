# Backends

Aura Connector gives teams **one typed model and query API** across several databases. The
component that executes your queries against a particular store is a **backend**.

## What a backend is

A backend is an adapter that takes the connector's canonical Query IR (the structured form
the query builder produces) and runs it against a concrete database, returning rows the
client hydrates into your typed models. Every backend implements the same small interface, so
the `Client` drives all of them uniformly and never branches on backend type. You write your
models and queries once; the backend determines where and how they execute.

## Backends versus transports

These are different layers, and keeping them distinct is what lets AuraDB stay fast while the
adapters stay simple:

```
Transport moves bytes.            Backend executes queries.
AuraDB backend  -> Aura Wire Protocol transport (TCP/TLS, or the in-process reference engine)
SQL backends    -> a database driver (aiosqlite / asyncpg / aiomysql)
MongoDB backend -> the MongoDB driver (motor)
Memory backend  -> the in-process reference engine
Redis backend   -> the Redis driver (cache / key-value use, not relational semantics)
```

A transport is only meaningful for the AuraDB and memory backends, which speak the binary
Aura Wire Protocol. The SQL, document, and key-value backends talk to their database driver
directly and expose no transport.

## Supported backends

| Backend          | DSN schemes                                              | Driver / engine            | Guide |
| ---------------- | -------------------------------------------------------- | -------------------------- | ----- |
| AuraDB (native)  | `auradb://`, `auradbs://`                                | Aura Wire Protocol 1 (TCP/TLS) | [AURADB.md](AURADB.md) — static-token auth and TLS; requires Aura Connector 0.3.0+ |
| AuraDB (reference) | `aura://`, `auras://`, `aura+tcp://`                   | Aura Wire Protocol (bundled) | The connector's bundled protocol/reference path; **not** the AuraDB network server |
| Memory           | `aura+memory://`, `memory://`                            | In-process reference engine| Full feature set, no external service |
| SQLite           | `sqlite://`, `sqlite+aiosqlite://`                       | `aiosqlite`                | [SQLITE.md](SQLITE.md) — first-class local backend |
| PostgreSQL       | `postgres://`, `postgresql://`, `postgresql+asyncpg://`  | `asyncpg`                  | [POSTGRESQL.md](POSTGRESQL.md) |
| MySQL / MariaDB  | `mysql://`, `mysql+aiomysql://`, `mariadb://`, `mariadb+aiomysql://` | `aiomysql`      | [MYSQL.md](MYSQL.md) |
| MongoDB          | `mongodb://`, `mongodb+motor://`                         | `motor`                    | [MONGODB.md](MONGODB.md) — document-native |
| Redis            | `redis://`, `redis+asyncio://`                           | `redis.asyncio`            | [REDIS.md](REDIS.md) — limited key-value / cache |

AuraDB remains the native, high-performance backend: it is the only backend that speaks the
Aura Wire Protocol and declares the `native_protocol` capability. The adapters exist so the
connector is useful immediately with infrastructure you already run.

### Native AuraDB backend

The `auradb://` and `auradbs://` schemes route to the native AuraDB backend. It opens its own
socket, performs the AWP 1 handshake, and speaks Aura Wire Protocol version 1 to a running
AuraDB server — including static-token authentication, TLS, and transactions with
read-your-writes. AWP 1 with auth and TLS first shipped in AuraDB v0.2.0 (the minimum native
server version); the current coordinated server is AuraDB v1.1.0. See [AURADB.md](AURADB.md)
for DSNs, authentication, TLS, transaction behaviour, and the version compatibility matrix.

> The legacy `aura://` / `auras://` / `aura+tcp://` schemes use the connector's bundled
> protocol/reference path and do **not** complete an AWP handshake with the AuraDB network
> server. To connect to a real AuraDB server, use `auradb://` (or `auradbs://` for TLS) with
> Aura Connector 0.3.0 or newer.

## Capability differences

Backends do not all support the same features, and Aura never pretends otherwise. Each backend
returns a [`BackendCapabilities`](BACKEND_CAPABILITY_MATRIX.md) value describing exactly what
it supports. Calling a feature a backend lacks — for example vector search on SQLite, or a
relational filter on Redis — raises `AuraBackendCapabilityError` rather than silently
producing wrong or emulated results. Inspect capabilities at runtime:

```python
async with Aura.connect("sqlite:///app.db", models=[User]) as db:
    caps = db.capabilities()          # or db.backend.capabilities()
    if caps.vector_search:
        ...
```

See the [capability matrix](BACKEND_CAPABILITY_MATRIX.md) for the full per-backend table.

## Installing backend extras

The base install is dependency-free. Install only the drivers you need:

```bash
pip install aura-connector[sqlite]
pip install aura-connector[postgres]
pip install aura-connector[mysql]
pip install aura-connector[mongodb]
pip install aura-connector[redis]

pip install aura-connector[sql]      # aiosqlite + asyncpg + aiomysql
pip install aura-connector[all-db]   # every database driver
```

If you select a backend whose driver is not installed, connecting raises
`AuraDriverNotInstalledError` with the exact install command, e.g.
`pip install aura-connector[postgres]`.

## Selecting a backend by DSN

The scheme of the DSN you pass to `Aura.connect(...)` selects the backend — nothing else
changes in your code:

```python
from aura import Aura

await Aura.connect("aura+memory://localhost/app", models=[User])      # memory
await Aura.connect("sqlite:///var/app.db", models=[User])              # SQLite (file)
await Aura.connect("sqlite://", models=[User])                         # SQLite (in-memory)
await Aura.connect("postgresql://u:p@localhost:5432/app", models=[User])
await Aura.connect("mysql://root:pw@localhost:3306/app", models=[User])
await Aura.connect("mongodb://localhost:27017/app", models=[User])
await Aura.connect("redis://localhost:6379/0", models=[Session])
await Aura.connect("auradb://db.example.com:7171/app", models=[User])  # AuraDB native (TCP)
await Aura.connect("auradbs://db.example.com:7171/app", models=[User]) # AuraDB native (TLS)
```

Schema objects (tables, collections, indexes) for the models you pass are created on connect
where the backend supports it; see [MIGRATIONS.md](MIGRATIONS.md) for the per-backend schema
and migration behaviour.
