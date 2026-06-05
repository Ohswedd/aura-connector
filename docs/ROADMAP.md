# Roadmap

This roadmap describes where Aura Connector is headed. It is a statement of
direction, not a delivery commitment. Items are grouped by theme and listed
roughly in the order we expect to approach them.

## Current release: 0.3.0

Aura Connector 0.3.0 is an async, typed Python data connector that exposes one
model and query API across AuraDB and several existing databases. It provides
typed models, an injection-safe query builder, first-class vector fields,
migrations, observability, an in-memory reference backend, and an optional Rust
native-acceleration extension with a guaranteed pure-Python fallback. Backend
feature differences are represented honestly through the
[backend capability matrix](BACKEND_CAPABILITY_MATRIX.md): an unsupported feature
raises a structured error rather than being emulated. See the
[CHANGELOG](../CHANGELOG.md) and [README](../README.md) for what is and is not
claimed.

## Delivered so far

- **Native AuraDB backend (0.3.0).** The `auradb://` and `auradbs://` schemes
  speak Aura Wire Protocol version 1 to a real AuraDB single-node server over TCP
  or TLS, with static-token authentication, server-verified TLS and optional
  mutual TLS, server-side cursor streaming, and read-your-writes transactions
  against AuraDB v0.2.x.
- **Multi-backend support (0.2.0).** Adapters for SQLite, PostgreSQL,
  MySQL/MariaDB, MongoDB, and Redis, plus an in-memory reference backend, behind
  the same typed model and query API and the backend capability matrix.
- **Typed core.** Declarative models, a fluent injection-safe query builder,
  vector fields, migrations, and observability (metrics and query fingerprints).
- **Optional native acceleration.** A Rust extension for hot paths (protocol
  checksums and vector packing) that is byte-for-byte identical to, and falls
  back to, the pure-Python implementation.

## AuraDB tracking

- Track AuraDB server releases over the Aura Wire Protocol and keep the native
  backend's capability advertisement aligned with the server it targets.
- Pin golden AWP frame and Query IR fixtures shared with the AuraDB conformance
  suite so both sides are checked against the same canonical encodings.

## Backends and capabilities

- Broaden coverage of the capability matrix per backend (for example richer
  document-path and full-text handling where the underlying engine supports it).
- Connection pooling and retry-policy refinements across backends.

## Query and models

- Additional query-builder surface (more aggregation and projection shapes) where
  it can be represented safely and uniformly across backends.
- Continued strictness in the typed layer (mypy strict) and model validation.

## Vectors and search

- Track approximate nearest-neighbour search on backends that gain it, while
  keeping exact search as the correctness baseline.
- Hybrid ranking shapes once the underlying backends expose them.

## Native acceleration

- Extend the optional native extension to more hot paths where it stays a
  transparent speed path with identical results to pure Python.
- Prebuilt wheels for more platforms.

## Ecosystem

- More worked examples and backend-specific guides.
- Expanded migration tooling.

## Non-goals (for now)

- Aura Connector is a client and connector library, not a database server; it
  does not provide storage, clustering, or replication itself. Those belong to
  the backends it talks to (AuraDB and the others).
- Emulating a backend feature that the underlying engine does not support. The
  capability matrix reports such features as unsupported rather than faking them.
