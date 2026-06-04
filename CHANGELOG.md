# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-04

Aura Connector evolves from an AuraDB-focused client into a multi-backend typed data
connector. AuraDB remains the native high-performance backend over the Aura Wire Protocol;
adapters make the same typed model and query API work with existing databases today. The
existing public API and the Aura Wire Protocol implementation are unchanged.

### Added

- Backend adapter architecture: a single backend interface the client drives uniformly, with
  the existing protocol path wrapped as the AuraDB and in-memory backends.
- SQLite backend (first-class, local, no external service required).
- PostgreSQL backend (asyncpg).
- MySQL/MariaDB backend (aiomysql).
- MongoDB backend (motor), document-native with structured queries.
- Redis limited key-value backend (redis.asyncio).
- Backend capability model and a per-backend capability matrix; unsupported features raise a
  structured capability error instead of being emulated.
- SQL compiler that translates the Query IR to parameterized SQL with bound values and
  quoted identifiers across the SQLite, PostgreSQL, and MySQL dialects.
- DSN routing for `sqlite`, `postgres`/`postgresql`, `mysql`/`mariadb`, `mongodb`, and
  `redis`, alongside the existing `aura`, `auras`, `aura+tcp`, `aura+memory`, and `memory`
  schemes.
- New public errors: `AuraBackendError`, `AuraBackendCapabilityError`, `AuraDialectError`,
  and `AuraDriverNotInstalledError`.
- Optional dependency extras: `sqlite`, `postgres`, `mysql`, `mongodb`, `redis`, `sql`, and
  `all-db`.
- Optional, environment-gated database integration test workflow and a
  `docker-compose.backends.yml` for local backend testing.
- Backend examples and documentation, including a backend capability matrix.

## [0.1.0] - 2026-06-04

Initial public release.

### Added

- Async-native client with connection lifecycle, context-manager usage, DSN parsing,
  timeouts, retry policy, pooling abstraction, health check, and ping.
- Typed model system with primary keys, optional fields, defaults and default factories,
  nested models, list and document fields, relationships, and first-class vector fields.
- Field system with `primary_key`, `unique`, `index`, defaults, aliases, descriptions,
  metadata, and relationship and vector hints.
- Fluent, injection-safe query builder that compiles to an immutable AST and Query IR,
  covering find, filter, order, limit, offset, include, select, insert, bulk insert,
  update, delete, upsert, count, exists, vector nearest-neighbour search, and a bounded
  raw-statement escape hatch.
- Deterministic binary wire protocol with framing, opcodes, flags, checksums, and length
  limits, covered by golden byte tests.
- Transport abstraction with a TCP transport and an in-memory reference server.
- Result hydration into typed model instances, including relationships and vectors.
- Schema generation and local migration diffing with a destructive-change CI gate.
- In-process observability: per-operation metrics, latency percentiles, secret-free query
  fingerprints, and an optional OpenTelemetry span bridge.
- Command-line interface for offline, server-independent tasks.
- Optional in-repository Rust/PyO3 native acceleration with a pure-Python fallback that is
  always available.

[Unreleased]: https://github.com/Ohswedd/aura-connector/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Ohswedd/aura-connector/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Ohswedd/aura-connector/releases/tag/v0.1.0
