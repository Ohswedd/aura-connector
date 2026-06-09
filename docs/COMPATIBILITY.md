# Compatibility

This page is the single source of truth for which Aura Connector version talks to
which AuraDB server version, and over which Aura Wire Protocol (AWP) revision.

## Connector ↔ AuraDB ↔ protocol

| Aura Connector | AuraDB server | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| 0.6.x          | 1.2.x         | AWP 1    | Supported, recommended — paired with AuraDB v1.2.0's query ergonomics (aggregations, terms facets, cooperative query timeouts). `.timeout(ms)` is enforced end-to-end; `query_timeout`/`transaction_timeout` map to `AuraTimeoutError`. A connector-native facets/aggregations API is planned for a follow-up; AuraDB serves them via the additive `aggregate` read request. AWP 1 unchanged. |
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

## Backend (database adapter) compatibility

The non-AuraDB backends (SQLite, PostgreSQL, MySQL, MongoDB, Redis) are unchanged
in 0.4.1. See [BACKEND_CAPABILITY_MATRIX.md](BACKEND_CAPABILITY_MATRIX.md) for the
per-backend capability grid and [BACKENDS.md](BACKENDS.md) for DSN routing.

## See also

- [AURADB.md](AURADB.md) — native AuraDB backend, auth, TLS, and cluster ergonomics.
- [ERRORS.md](ERRORS.md) — the typed error hierarchy, including `AuraNotLeaderError`.
- [TRANSACTIONS.md](TRANSACTIONS.md) — transaction semantics and cluster-preview rules.
