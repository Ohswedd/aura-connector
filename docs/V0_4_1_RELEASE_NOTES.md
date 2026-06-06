# Aura Connector v0.4.1 release notes

Coordinated **patch** release with AuraDB v0.7.1, focused on **cluster-preview connector
ergonomics polish**. AuraDB's multi-node mode is experimental and opt-in; single-node mode
remains the recommended production deployment. This release adds **no** new database
architecture, **no** breaking API changes, and **no** production high availability,
automatic failover, distributed transactions, or linearizable follower reads. The Aura Wire
Protocol version (AWP 1) is unchanged, and all v0.4.0 behavior is preserved.

## What's new

### Clearer `AuraNotLeaderError` messages

`str(error)` is now self-documenting. It names the non-leader node that was reached, where
the leader is (or that the leader is unknown), the retry classification, and exactly how to
redirect:

```
[not_leader] this node is not the leader (reached non-leader node 0000000000000001)
(leader at 10.0.0.2:7171) (retry on the leader with Client.connect_to_leader(error) or
reconnect_to('10.0.0.2:7171'); writes are not retried automatically)
```

When no leader address is known, it says so and points at `auradb cluster leader` rather than
implying a safe automatic retry. The string and `repr` render only node ids and a `host:port`
address — never an auth token or TLS material. The structured attributes (`leader_addr`,
`leader_client_addr`, `leader_hint`, `leader_node_id`, `current_node_id`, `retryable`,
`raw_payload`) are unchanged.

### Secure-by-default redirects

`Client.connect_to_leader` / `Client.reconnect_to` already preserved the original scheme,
token auth, and TLS across a redirect. 0.4.1 hardens the edges:

- An explicit **insecure** redirect target (e.g. `auradb://leader:7171`) on a TLS client is
  **refused** rather than silently dropping TLS. Pass `allow_insecure=True` to override.
- An **unknown DSN scheme** in a redirect address is rejected with an actionable error.
- Hostname verification is never silently disabled; a bare `host:port` redirect inherits the
  original client's TLS, and the TLS server name is recalculated to the leader host.

### More examples

A new `examples/auradb_leader_redirect.py` shows the three safe ways to reach the leader:
out-of-band discovery via `auradb cluster leader`, explicit reconnect on `AuraNotLeaderError`,
and the opt-in bounded redirect helper. The existing `auradb_cluster.py` and
`auradb_not_leader.py` cross-reference it.

### Documentation

- `docs/TRANSACTIONS.md` — transaction semantics and the cluster-preview redirect-safety
  rules (node-local transaction ids, restart-on-leader, no mid-stream redirect).
- `docs/COMPATIBILITY.md` — the connector ↔ AuraDB ↔ protocol matrix.
- Updated `AURADB.md`, `CLIENT.md`, `TESTING.md`, and `README.md`.

### Tests and conformance

- `tests/unit/test_redirect_security.py` — TLS/auth preservation, secure-by-default redirect,
  unknown-scheme rejection, and diagnostic-message coverage.
- New `__str__` guidance tests in `tests/unit/test_not_leader.py`.
- Explicitly-named live conformance checks in `tests/integration/test_auradb_cluster_live.py`,
  gated on `AURADB_CLUSTER_LEADER_DSN` / `AURADB_CLUSTER_FOLLOWER_DSN` (with optional
  `AURADB_CLUSTER_TOKEN`, `AURADB_CLUSTER_CA`, `AURADB_CLUSTER_SERVER_NAME`).

## Fixed

- A static-type annotation on the native backend's server-error map so the `not_leader`
  mapping path type-checks cleanly under `mypy` (no behavior change).

## Compatibility

| Aura Connector | AuraDB server | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| 0.4.1          | 0.7.x         | AWP 1    | Supported — cluster-preview ergonomics |
| 0.4.1          | 0.2.x         | AWP 1    | Supported — cluster ergonomics simply never trigger on a single node |
| 0.3.x          | 0.7.x / 0.2.x | AWP 1    | Supported — native backend, auth + TLS, without the typed `not_leader` ergonomics |

See [COMPATIBILITY.md](COMPATIBILITY.md) for the full matrix.
