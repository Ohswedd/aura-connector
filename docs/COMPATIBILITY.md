# Compatibility

This page is the single source of truth for which Aura Connector version talks to
which AuraDB server version, and over which Aura Wire Protocol (AWP) revision.

## Connector ↔ AuraDB ↔ protocol

| Aura Connector | AuraDB server | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| 0.4.x          | 0.7.x         | AWP 1    | Supported — cluster-preview ergonomics (`AuraNotLeaderError`, leader redirect helpers). 0.4.1 is a docs/ergonomics polish over 0.4.0; the wire surface is identical. |
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

## Backend (database adapter) compatibility

The non-AuraDB backends (SQLite, PostgreSQL, MySQL, MongoDB, Redis) are unchanged
in 0.4.1. See [BACKEND_CAPABILITY_MATRIX.md](BACKEND_CAPABILITY_MATRIX.md) for the
per-backend capability grid and [BACKENDS.md](BACKENDS.md) for DSN routing.

## See also

- [AURADB.md](AURADB.md) — native AuraDB backend, auth, TLS, and cluster ergonomics.
- [ERRORS.md](ERRORS.md) — the typed error hierarchy, including `AuraNotLeaderError`.
- [TRANSACTIONS.md](TRANSACTIONS.md) — transaction semantics and cluster-preview rules.
