# Compatibility

This page is the single source of truth for which Aura Connector version talks to
which AuraDB server version, and over which Aura Wire Protocol (AWP) revision.

## Connector ↔ AuraDB ↔ protocol

| Aura Connector | AuraDB server | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| 0.8.0 (planning) | 1.4.x / 1.3.x | AWP 1  | In progress, additive — pairs with the AuraDB v1.4.0 line. Adds client-side ergonomics only: environment-driven connection profiles (`ConnectionProfile` / `from_profile`), capability UX (`capabilities().require(...)` / `.describe()`), and typed parsers for AuraDB's `search eval` / `vector eval` reports (`aura.search_quality`). **No protocol change, no new wire feature, no breaking API change**; existing `connect(...)` / `Client(...)` constructors are unchanged and it keeps working against v1.3.x servers. The relevance reports are produced by the AuraDB CLI, not the connector. |
| 0.7.0          | 1.3.x         | AWP 1    | Supported, recommended — paired with AuraDB v1.3.0. Adds the v1.3 query ergonomics: group-by aggregation (`group_by`), approximate-vector (HNSW) preview options incl. a `fallback` policy (`HnswOptions`), a best-effort query profile (`profile()` / `QueryProfile`), and public ranked-search cursor resume (`Page`/`SearchPage`, `resume_search`, `builder.page`). New capability flags `group_by`/`query_profile`/`hnsw_preview`/`cursor_resume` gate each feature; the native backend negotiates them at handshake. All additive and backward compatible; AWP 1 unchanged. |
| 0.7.0          | 1.2.x / 1.1.x | AWP 1    | Supported for those servers' features. The v1.3 features are gated off when the server does not advertise the matching capability (a clear `AuraCapabilityError` rather than a silently-wrong result). |
| 0.6.1          | 1.2.x         | AWP 1    | Supported — paired with AuraDB v1.2.1. Drives the v1.2 query ergonomics: aggregations (`count`/`min`/`max`), terms facets (`facet`/`aggregate`), ranked pagination (`search_pages`), and cooperative query timeouts (`timeout(ms)`). v0.6.1 forwards the per-query `timeout_ms` to the wire so `.timeout(ms)` is enforced end-to-end by AuraDB; `query_timeout`/`transaction_timeout` map to `AuraTimeoutError`. Live over-the-wire conformance ships with AuraDB v1.2.1. AWP 1 unchanged. |
| 0.6.0          | 1.2.x         | AWP 1    | Supported — same query-ergonomics API surface (aggregations, terms facets, ranked pagination). **Known limitation:** the per-query `timeout_ms` is not forwarded for the AuraDB backend, so `.timeout(ms)` is silently dropped against AuraDB; upgrade to 0.6.1 for enforced timeouts. |
| 0.6.x          | 1.1.x         | AWP 1    | Supported for the 1.1.x feature set; `timeout_ms` is ignored by pre-1.2.0 servers (no error). |
| 0.5.x          | 1.2.x         | AWP 1    | Supported for pre-1.2 features against a 1.2.0 server. |
| 0.5.x          | 1.1.x         | AWP 1    | Supported — first-class search and ranking APIs (`search_text` BM25, `search_vector`, `search_hybrid`), typed scores, capability negotiation. The new clauses are additive Query IR; the wire revision is unchanged. |
| 0.5.x          | 1.0.x         | AWP 1    | Non-search operations supported. The connector reads the server's advertised capabilities at handshake; a `search_text`/`search_hybrid` call against a pre-1.1.0 server raises `AuraCapabilityError` (the server does not advertise BM25/hybrid) rather than returning a silently-wrong result. |
| 0.4.x          | 0.7.x / 1.0.x / 1.1.x | AWP 1 | Supported for non-search operations — cluster-preview ergonomics (`AuraNotLeaderError`, leader redirect helpers). 0.4.x predates the search APIs, so search/ranking is unavailable, but basic CRUD/transactions/vector against a 1.1.0 server work (AWP 1 unchanged). |
| 0.4.x          | 0.2.x         | AWP 1    | Supported — the cluster fields are additive; against a single-node server the cluster ergonomics simply never trigger. |
| 0.3.x          | 0.7.x / 0.2.x | AWP 1    | Supported — native backend (`auradb://`, `auradbs://`), static-token auth + TLS. Does not surface the typed `not_leader` ergonomics. |
| 0.2.x          | any           | n/a      | Not supported for the native AWP path — 0.2.x predates the authenticated, TLS-capable native backend. |

Notes:

- **AWP is unchanged.** Aura Connector 0.4.x speaks AWP 1, the same revision as
  0.3.x. The cluster-preview fields (`not_leader` leader hints) are purely
  additive to the error payload; older clients ignore them.
- **0.4.1 is a patch over 0.4.0.** It adds clearer `AuraNotLeaderError` messages,
  more redirect/reconnect examples, TLS/auth redirect edge-case coverage, and
  transaction-redirect documentation. There are **no breaking API changes** and no
  new database architecture.
- **Cluster mode is experimental and opt-in.** There is no production high
  availability, automatic failover, distributed transactions, or linearizable
  follower reads. Single-node AuraDB remains the recommended production path.
- **Transaction isolation is snapshot isolation** with optimistic (first-committer-wins)
  conflict detection on commit — not serializable isolation. `transaction(isolation=…)`
  defaults to `"snapshot"`; `"serializable"` is accepted only as a deprecated compatibility
  alias for snapshot isolation and does not change AuraDB semantics.
- **The 0.8.0 ergonomics are convenience layers, not new guarantees.** `ConnectionProfile` is
  not a secret manager (it redacts the token from `repr` but does not store or rotate it);
  capability helpers read the current payload and require no server change; the search-quality
  parsers only read CLI output and compute nothing. No production HA automation, no unbounded
  retries, and AuraDB-only features still require an AuraDB backend that advertises them.

## Backend (database adapter) compatibility

The non-AuraDB backends (SQLite, PostgreSQL, MySQL, MongoDB, Redis) are unchanged
in 0.4.1. See [BACKEND_CAPABILITY_MATRIX.md](BACKEND_CAPABILITY_MATRIX.md) for the
per-backend capability grid and [BACKENDS.md](BACKENDS.md) for DSN routing.

## See also

- [AURADB.md](AURADB.md) — native AuraDB backend, auth, TLS, and cluster ergonomics.
- [ERRORS.md](ERRORS.md) — the typed error hierarchy, including `AuraNotLeaderError`.
- [TRANSACTIONS.md](TRANSACTIONS.md) — transaction semantics and cluster-preview rules.
