# AuraDB boundary

Aura Connector is a client and connector library. It implements everything needed to
model data, build queries, speak the wire protocol, and execute work against a server,
plus a complete in-memory reference server for local development and testing.

The AuraDB server itself is a separate project. This page states exactly where the
connector ends and the server begins, so nothing on either side is overclaimed.

AuraDB remains the **native, high-performance backend** over the Aura Wire Protocol. The
SQL, document, and key-value [backends](BACKENDS.md) are adapters that make the same typed
API useful with databases you already run; they cover a subset of AuraDB's features, declared
honestly in the [capability matrix](BACKEND_CAPABILITY_MATRIX.md). The boundary described
below is specifically the connector↔AuraDB-server boundary.

## Implemented in this package

- Typed model system, fields, and first-class vector fields.
- Fluent, injection-safe query builder that compiles to an immutable AST and Query IR.
- Deterministic binary wire protocol with framing, checksums, and length limits.
- Transport abstraction with a TCP transport and an in-memory reference transport.
- A backend adapter layer: AuraDB (native protocol), the in-memory reference engine, and
  SQLite, PostgreSQL, MySQL/MariaDB, MongoDB, and Redis adapters.
- Result hydration into typed model instances, including relationships and vectors.
- Schema generation and local migration diffing with a destructive-change CI gate.
- In-process observability: metrics, latency percentiles, and query fingerprints.
- A command-line interface for offline, server-independent tasks.

## Provided by the AuraDB server (not this package)

These belong to the server and are intentionally not implemented or simulated here:

| Capability | Why it is server-side |
|---|---|
| Durable storage and indexing | Requires the storage engine. |
| Applying migrations to a live cluster | Requires a connected, writable server. |
| Server-side cost-based query planning | Requires live statistics, cardinality, and index metadata. |
| Lock and impact estimation on a live cluster | Requires the running cluster's state. |
| Distributed transaction coordination | Cross-shard isolation is a server responsibility; the client drives begin, commit, and rollback over the protocol. |

## How the boundary behaves

Operations that genuinely require a live cluster fail with a structured `AuraError` that
explains the missing dependency. The connector never returns a fabricated success or an
empty result in place of real server work.

Local, server-independent operations (schema compilation, migration diffing, client-side
plan rendering, fingerprinting, and the full test suite) run against the in-memory
reference server with no external database.

## Reference server

The in-memory reference server is a real implementation of the protocol and a query
engine, reached through the `aura+memory://` DSN scheme. It exists so the examples and
tests exercise the same protocol and hydration path as a network transport, without
requiring a deployed AuraDB cluster.
