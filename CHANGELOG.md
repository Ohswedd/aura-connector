# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Ohswedd/aura-connector/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Ohswedd/aura-connector/releases/tag/v0.1.0
