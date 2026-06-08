# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-06-08

Coordinated release with AuraDB v1.1.0, adding first-class connector support for the new
AuraDB search and ranking features. The Aura Wire Protocol version (AWP 1) is unchanged —
the new query clauses are additive Query IR and response fields. AuraDB single-node remains
the recommended production deployment; multi-node remains an HA candidate preview, not
production high availability. Approximate (ANN) vector search is not implemented in AuraDB
v1.1.0; exact vector search remains the correctness baseline.

### Added

- `QueryBuilder.search_text(field, query, *, rank="bm25", operator="or", k1=, b=, limit=)`:
  ranked full-text (BM25) search returning documents ordered by relevance, with optional
  AND term semantics and tunable BM25 parameters.
- `QueryBuilder.search_vector(field, vector, *, metric=, top_k=)`: exact vector
  nearest-neighbour search (a clearer alias over `nearest`).
- `QueryBuilder.search_hybrid(text_field, query, vector_field, vector, *, weights=, fusion=,
  top_k=, metric=, operator=)`: hybrid text-plus-vector retrieval with `weighted_sum` or
  `reciprocal_rank_fusion` score fusion.
- Typed result scores: `aura.search_scores(instance)` returns a `SearchScores` with `score`,
  `text_score`, `vector_score`, and the 1-based `rank` (populated for ranked queries).
- Capability negotiation: ranked-search queries are pre-checked against the backend's
  advertised capabilities and raise `AuraCapabilityError` (alias of
  `AuraBackendCapabilityError`) when a backend does not support the requested feature, rather
  than silently dropping the clause. SQL/Mongo/Redis backends raise; the AuraDB native and
  in-memory reference backends support BM25 and hybrid search.
- Examples: `examples/auradb_text_search.py`, `examples/auradb_hybrid_search.py`, and
  `examples/auradb_explain_analyze.py`.
- The in-process reference engine implements BM25 ranking and hybrid fusion so the search
  APIs are exercised end-to-end in the default test suite with no external service.

### Changed

- Bumped to v0.5.0 and paired with AuraDB v1.1.0 (tested 1.1.0, supported 1.1.x).
- Existing `nearest`, `similar_to`, `text`, and `fusion` builder methods are unchanged and
  remain supported.
- `transaction(isolation=…)` now defaults to `"snapshot"` instead of `"serializable"`, matching
  AuraDB's actual guarantee — snapshot isolation with optimistic (first-committer-wins) conflict
  detection. The connector does not provide serializable isolation. The `"serializable"` token is
  still accepted as a deprecated compatibility alias that maps to snapshot isolation, so existing
  callers do not break; it does not change AuraDB transaction semantics. No AuraDB server or wire
  changes — the server already applies snapshot isolation regardless of the token.

## [0.4.1] - 2026-06-06

Coordinated patch release with AuraDB v0.7.1, focused on cluster-preview connector
ergonomics. No new database architecture, no breaking API changes, and the Aura Wire
Protocol version (AWP 1) is unchanged. AuraDB's multi-node mode remains experimental and
opt-in; single-node mode is the recommended production deployment. There is no production
high availability, automatic failover, or distributed transactions.

### Added

- Clearer `AuraNotLeaderError` messages: `str(error)` now names the non-leader node reached,
  the leader address (or that it is unknown), and how to redirect (`Client.connect_to_leader`
  / `reconnect_to`), and is explicit that writes are never retried automatically. It still
  renders only node ids and a `host:port` address — never auth tokens or TLS material.
- Additional cluster redirect and reconnect examples, including a new
  `examples/auradb_leader_redirect.py` covering leader discovery via `auradb cluster leader`,
  explicit reconnect, and the bounded redirect helper.
- TLS/auth redirect edge-case tests (`tests/unit/test_redirect_security.py`): auth and TLS are
  preserved across a redirect, hostname verification is never silently disabled, a bare
  `host:port` redirect inherits the original TLS, and a secure client refuses an explicit
  plaintext redirect target by default.
- Transaction redirect safety documentation (`docs/TRANSACTIONS.md`) and tests covering the
  node-local transaction-id and restart-on-leader rules.
- Connector ↔ AuraDB compatibility matrix (`docs/COMPATIBILITY.md`).
- Cluster conformance hardening: explicitly-named live checks (`AURADB_CLUSTER_*`,
  including an optional `AURADB_CLUSTER_SERVER_NAME`) in
  `tests/integration/test_auradb_cluster_live.py`.

### Changed

- Improved leader redirect helper diagnostics without changing default behavior: redirect
  addresses now reject unknown DSN schemes, and a TLS client fails closed on an explicit
  insecure redirect target (override with `allow_insecure=True`).
- Improved AuraDB cluster-preview docs (`AURADB.md`, `CLIENT.md`, `TESTING.md`, `README.md`)
  and the streaming-redirect rejection message (it now points at re-issuing the query on the
  leader).

### Fixed

- Corrected a static-type annotation on the native backend's server-error map so the
  `not_leader` mapping path type-checks cleanly under `mypy` (no behavior change).

## [0.4.0] - 2026-06-06

Coordinated release with AuraDB v0.7.0, focused on cluster-preview ergonomics. AuraDB's
multi-node mode is experimental and opt-in; single-node mode remains the recommended
production deployment. There is no production high availability, automatic failover, or
distributed transactions. Non-cluster behavior is unchanged, and the Aura Wire Protocol
version (AWP 1) is unchanged — the new fields are purely additive.

### Added

- Dedicated `AuraNotLeaderError` for AuraDB cluster-preview `not_leader` responses. It maps
  from the server's `not_leader` error code and exposes the leader-routing hints the server
  provided — `leader_addr`, `leader_client_addr`, `leader_hint`, `leader_node_id`,
  `current_node_id`, `retryable`, and the full `raw_payload` — extracted from either the
  top level of the payload or a nested `not_leader` object, and never raising on a missing
  field. `str(error)` includes a usable leader address when known.
- Safe manual reconnect helpers `Client.connect_to_leader(error)` and
  `Client.reconnect_to(address)`: they open a new client bound to the leader, preserving the
  original scheme, token authentication, and TLS settings, and carry no transaction state.
- Optional, bounded leader-redirect helper `Client.with_leader_redirect(max_redirects=...)`
  returning a `LeaderRedirect` wrapper. It is disabled by default and only ever redirects on
  an explicit `not_leader` response that carries a usable leader address, never more than
  `max_redirects` times. It refuses to wrap transactions or streaming cursors.
- Cluster-aware examples `examples/auradb_cluster.py` and `examples/auradb_not_leader.py`.
- Cluster conformance coverage against AuraDB v0.7.x in
  `tests/integration/test_auradb_cluster_live.py`, gated on `AURADB_CLUSTER_LEADER_DSN` /
  `AURADB_CLUSTER_FOLLOWER_DSN` (with optional `AURADB_CLUSTER_TOKEN` / `AURADB_CLUSTER_CA`).

### Changed

- Improved AuraDB cluster-preview error ergonomics while preserving existing non-cluster
  behavior: known non-cluster error codes map exactly as before, and unknown codes still
  fall back to `AuraServerError`.

### Compatibility

- Aura Connector 0.4.x works with AuraDB 0.7.x over AWP 1, and remains compatible with
  AuraDB 0.6.x single-node servers (which never send `not_leader`). The cluster ergonomics
  activate only when a server returns a `not_leader` response.

## [0.3.0] - 2026-06-04

Coordinated release with AuraDB v0.2.0. This adds a native AuraDB backend that speaks the
Aura Wire Protocol version 1 (AWP 1) to a real AuraDB single-node server over TCP or TLS,
including static-token authentication. The earlier in-process reference path (the `aura://`
and `memory://` schemes) is unchanged.

### Added

- Native AuraDB backend reachable via the `auradb://` (plaintext) and `auradbs://` (TLS) DSN
  schemes. It connects to a running AuraDB server, performs the AWP 1 handshake, translates
  the Query IR to the server's Query IR and results back, and exposes the full client API:
  schema create, insert, bulk insert, find, filter, document-path filters, full-text search,
  count, exists, update, delete, upsert, vector nearest, server-side cursor streaming, and
  transactions.
- Static-token authentication for the native backend via `TokenAuth`: the token is presented
  in the AWP handshake and an authentication failure raises `AuraAuthenticationError`.
- TLS for the native backend via `TLSConfig` (CA trust, hostname verification, and optional
  client certificates for mutual TLS).
- AWP 1 codec (`aura.protocol.awp1`): a 44-byte framed, CRC32-checked wire format matching the
  AuraDB server.

- Transactions against AuraDB v0.2.0: `begin`/`commit`/`rollback` with read-your-writes.
  Reads issued inside a transaction (find, filter, count, exists, vector nearest,
  document-path, full-text, and cursor paging) observe the transaction's own staged writes
  and not its staged deletes; those effects stay invisible to other connections until commit.
  This relies on AuraDB v0.2.0's transaction-scoped reads, so it is the minimum server version
  for transactional reads to behave correctly.

### Compatibility

- Aura Connector 0.3.x works with AuraDB 0.2.x over AWP 1 (`auradb://` plaintext, `auradbs://`
  TLS), including static-token authentication and server-verified TLS.
- Aura Connector 0.2.x does **not** speak the new authenticated, TLS-capable native AWP path
  and cannot complete an AWP handshake with the AuraDB v0.2.0 network server. Upgrade to 0.3.x
  to talk to AuraDB v0.2.0.

### Notes

- AuraDB server compatibility: this release targets AuraDB v0.2.0 (AWP 1). Connect with
  `auradb://host:7171/db` (or `auradbs://...` for TLS).

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

[Unreleased]: https://github.com/Ohswedd/aura-connector/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Ohswedd/aura-connector/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Ohswedd/aura-connector/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Ohswedd/aura-connector/releases/tag/v0.1.0
