# Aura Connector v0.4.0 release notes

Coordinated release with AuraDB v0.7.0, focused on **cluster-preview ergonomics**. AuraDB's
multi-node mode is experimental and opt-in; single-node mode remains the recommended
production deployment. This release adds no production high availability, automatic failover,
or distributed transactions, and does not change the Aura Wire Protocol version — the new
fields are purely additive, and non-cluster behavior is unchanged.

## What's new

### `AuraNotLeaderError`

A dedicated exception (`aura.AuraNotLeaderError`) for AuraDB's `not_leader` response, raised
when a write reaches a follower in the multi-node preview. It maps from the server's
`not_leader` error code on both the native (`auradb://`, `auradbs://`) and reference paths,
and exposes the leader-routing hints the server provided:

- `leader_addr` — best usable client-facing address of the current leader
- `leader_client_addr` — the leader's declared client address, as reported
- `leader_hint` — a free-form leader hint string, when provided
- `leader_node_id` — the recognized leader's node id
- `current_node_id` — the non-leader node that was reached
- `retryable` — `True` when a leader is known
- `raw_payload` — the full structured server payload

Hints are read from either the top level of the payload or a nested `not_leader` object, and
a missing field never raises. `str(error)` includes a usable leader address when known.

### Manual reconnect helpers

- `await client.connect_to_leader(error)` — open a new client bound to the leader named by a
  caught `AuraNotLeaderError`.
- `await client.reconnect_to("host:port")` — the lower-level form for a known address.

Both return a new, independent client that inherits the original scheme, token authentication,
and TLS settings unchanged (verification is never silently weakened), and carry no transaction
state. They raise a clear error when no leader address is available or for non-AuraDB backends.

### Optional bounded leader redirect

`client.with_leader_redirect(max_redirects=1)` returns a `LeaderRedirect` wrapper that retries
an operation on the leader, bounded by `max_redirects`. It is disabled by default and only
ever redirects on a `not_leader` response with a usable leader address — safe because
`not_leader` is a pre-application rejection (a follower refuses a write before it enters the
Raft log). It refuses to wrap transactions or streaming cursors.

### Examples and conformance

- `examples/auradb_cluster.py` and `examples/auradb_not_leader.py` — cluster-aware usage.
- `tests/integration/test_auradb_cluster_live.py` — cluster conformance against AuraDB v0.7.x,
  gated on `AURADB_CLUSTER_LEADER_DSN` / `AURADB_CLUSTER_FOLLOWER_DSN`.

## Compatibility

Aura Connector 0.4.x works with AuraDB 0.7.x over AWP 1 and remains compatible with AuraDB
0.6.x single-node servers (which never send `not_leader`). The cluster ergonomics activate
only when a server returns a `not_leader` response, so upgrading is safe for single-node
users.

## Upgrading

`pip install -U aura-connector`. No code changes are required for existing single-node or
non-cluster usage. To adopt the cluster preview, catch `AuraNotLeaderError` and use
`connect_to_leader` or `with_leader_redirect`.
