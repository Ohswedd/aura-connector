# Native AuraDB backend

The **native AuraDB backend** speaks the Aura Wire Protocol version 1 (AWP 1) to a running
[AuraDB](https://github.com/Ohswedd/auradb) server over TCP or TLS. It opens its own socket,
performs the AWP handshake (with optional static-token authentication), translates the
connector's canonical Query IR to the server's Query IR, and decodes results back into your
typed models. AWP 1 with auth and TLS first shipped in AuraDB v0.2.0, so **v0.2.0 is the
minimum native server version**; the current coordinated server is AuraDB v1.2.1 (see
[COMPATIBILITY.md](COMPATIBILITY.md)).

This connector release (v0.6.1) is a conformance and documentation hardening release over
v0.6.0: v0.6.0 introduced the vector and query ergonomics surface (aggregations, terms
facets, ranked pagination, cooperative query timeouts, opt-in HNSW preview), and v0.6.1
adds no API or behavior changes beyond forwarding the per-query `timeout_ms` to the wire so
`.timeout(ms)` is enforced by AuraDB v1.2.x. Facets, aggregations, ranked pagination, and
query timeouts require AuraDB v1.2.x capabilities; a backend that cannot serve a requested
feature raises `AuraCapabilityError` rather than pretending to support it. The default
transaction isolation remains `snapshot`. AuraDB v1.2.1 ships live over-the-wire conformance
scripts (facets/aggregations, ranked pagination, query timeouts, and cluster variants) that
exercise this connector against a running server.

The same typed model and query API you use with every other backend works unchanged — only
the DSN changes.

## DSN schemes

| Scheme       | Transport     | Use for |
| ------------ | ------------- | ------- |
| `auradb://`  | plaintext TCP | A local or trusted-network AuraDB server |
| `auradbs://` | TLS           | A remote AuraDB server (TLS terminated by the server) |

```python
from aura import connect

# Plaintext (loopback / trusted network):
async with connect("auradb://127.0.0.1:7171/app", models=[User]) as client:
    ...

# TLS:
async with connect("auradbs://db.example.com:7171/app", models=[User]) as client:
    ...
```

The path segment (`/app` above) names the logical database/namespace.

### Relationship to the legacy `aura://` schemes

The pre-existing `aura://`, `auras://`, and `aura+tcp://` schemes select the connector's
**bundled protocol/reference path** and are retained for backward compatibility. They do
**not** complete an AWP handshake with the AuraDB network server. To connect to a real
AuraDB server, use `auradb://` (or `auradbs://`). The `aura+memory://` and `memory://`
schemes continue to drive the in-process reference engine for tests and local development.

## Authentication

AuraDB enforces static-token authentication when the server enables it. Provide a
token with `TokenAuth`:

```python
from aura import connect
from aura.config import TokenAuth

async with connect(
    "auradb://127.0.0.1:7171/app",
    models=[User],
    auth=TokenAuth("my-secret-token"),
) as client:
    ...
```

**Handshake behaviour.** On connect the backend sends a HELLO carrying the token in the
`auth_token` field (the authentication fast path). The server replies with an ack reporting
`auth_required` and `authenticated`. If the server requires authentication and the fast path
did not authenticate the session, the backend sends a dedicated AUTH frame with the token.

**Failure behaviour.** If the server requires authentication and no token was supplied, or the
token is rejected, the backend raises `AuraAuthenticationError`. The token is redacted from
`repr()` output and never logged. Only `TokenAuth` is supported by this backend; passing
another auth type raises `AuraAuthenticationError` with guidance to use `TokenAuth`.

## TLS

Use the `auradbs://` scheme, or pass a `TLSConfig` explicitly:

```python
from aura import connect
from aura.config import TLSConfig, TokenAuth

async with connect(
    "auradbs://db.example.com:7171/app",
    models=[User],
    auth=TokenAuth("my-secret-token"),
    tls=TLSConfig(
        enabled=True,
        ca_cert_path="/etc/aura/ca.pem",   # CA bundle that signed the server cert
        verify_hostname=True,              # verify the server hostname (default)
        client_cert_path=None,             # set with client_key_path for mutual TLS
    ),
) as client:
    ...
```

- **CA file.** `ca_cert_path` is the PEM CA bundle the client trusts to validate the server
  certificate. With no CA file the system trust store is used.
- **Server verification.** `verify_hostname=True` (the default) verifies the server hostname
  against the certificate. The `auradbs://` scheme enables TLS automatically.
- **Failure behaviour.** A certificate the CA does not validate, or a hostname that does not
  match the certificate (when `verify_hostname=True`), fails the TLS handshake and connecting
  raises a connection error — the client never silently downgrades to plaintext.
- **Mutual TLS.** Set both `client_cert_path` and `client_key_path` to present a client
  certificate when the server requires one.

`verify_hostname=False` disables certificate hostname checking and is intended only for
development against self-signed certificates.

## Transactions

The native backend supports `begin` / `commit` / `rollback` with **read-your-writes** against
an AuraDB server:

```python
async with client.transaction() as tx:
    await tx.insert(User(id=1, email="ada@example.com"))
    # The transaction sees its own staged write:
    assert await tx.query(User).where(User.id == 1).count() == 1
    # A non-transactional read on another connection does not, until commit.
# On clean exit the transaction commits; on an exception it rolls back.
```

Reads issued inside a transaction (find, filter, count, exists, vector nearest, document-path
filters, full-text search, and cursor paging) carry the server-side transaction id, so they
observe the transaction's own staged writes and do **not** observe its staged deletes. These
effects stay invisible to other connections until commit. This relies on the server's
transaction-scoped reads; **AuraDB v0.2.0 is the minimum server version** for transactional
reads to behave correctly.

The isolation model is **snapshot isolation**: read-your-writes over committed state with
optimistic (first-committer-wins) conflict detection on commit. It is not serializable
isolation, and the connector does not upgrade AuraDB transactions to serializable isolation.

## Compatibility

[COMPATIBILITY.md](COMPATIBILITY.md) is the authoritative matrix. In brief:

| Aura Connector | AuraDB        | Protocol | Status |
| -------------- | ------------- | -------- | ------ |
| 0.5.x          | 1.1.x         | AWP 1    | Recommended — first-class search and ranking (`search_text`, `search_vector`, `search_hybrid`) |
| 0.5.x          | 1.0.x         | AWP 1    | Supported — basic operations; search APIs raise `AuraCapabilityError` (server predates BM25/hybrid) |
| 0.4.x          | 0.7.x / 1.x   | AWP 1    | Supported — cluster-preview ergonomics (`AuraNotLeaderError`, leader redirect); no search APIs |
| 0.3.x          | 0.2.x+        | AWP 1    | Supported — native backend (`auradb://`, `auradbs://`), auth + TLS |
| 0.2.x          | —             | n/a      | Not supported — 0.2.x does not speak the authenticated, TLS-capable native AWP path |

- **Aura Connector 0.2.x does not** speak the authenticated, TLS-capable native AWP path and
  cannot complete an AWP handshake with the AuraDB network server. Upgrade to 0.3.x or newer.

## Cluster preview ergonomics (AuraDB multi-node)

AuraDB's multi-node mode is an **experimental, opt-in preview**. There is no production
high availability, no automatic failover, and no distributed transactions; single-node mode
remains the recommended production deployment. In the preview, only the Raft **leader**
accepts writes. A write sent to a follower is rejected with a structured `not_leader`
response rather than being silently forwarded.

The connector maps that response to a dedicated `AuraNotLeaderError` (see
[`ERRORS.md`](ERRORS.md)) carrying the leader-routing hints the server provided —
`leader_addr`, `leader_client_addr`, `leader_hint`, `leader_node_id`, `current_node_id`,
`retryable`, and `raw_payload`. None of these helpers run unless you opt in, and the Aura
Wire Protocol version is unchanged (the cluster fields are purely additive).

### Reconnect to the leader (manual, explicit)

```python
from aura import AuraNotLeaderError, connect

async with connect("auradbs://follower:7171/app", models=[Note], auth=token) as client:
    try:
        await client.insert(Note(id=1, body="hi"))
    except AuraNotLeaderError as exc:
        if exc.leader_addr is None:
            raise  # no leader known yet — resolve via `auradb cluster leader`
        # A new client bound to the leader; token auth and TLS are preserved.
        leader = await client.connect_to_leader(exc)
        try:
            await leader.insert(Note(id=1, body="hi"))
        finally:
            await leader.close()
```

`Client.reconnect_to("host:port")` is the lower-level form when you already know the
address. Both return a *new*, independent client (no shared transaction state) and inherit
the original scheme, authentication, and TLS configuration unchanged — certificate
verification is never silently weakened.

**Secure by default.** A leader hint from a `not_leader` response is a bare `host:port`, so a
redirect keeps the original client's TLS and auth. If you pass an *explicit* plaintext
address (for example `auradb://leader:7171`) while the client is on a TLS scheme, the
redirect is **refused** rather than silently dropping TLS; pass `allow_insecure=True` to
`connect_to_leader` / `reconnect_to` to override deliberately. An unknown DSN scheme in a
redirect address is rejected outright.

### Bounded leader redirect (opt-in)

```python
leader = client.with_leader_redirect(max_redirects=1)
await leader.insert(Note(id=2, body="auto-redirected"))
```

`with_leader_redirect` is **disabled by default**. The returned `LeaderRedirect` retries an
operation against the current leader **only** on a `not_leader` response that carries a
usable leader address, and never more than `max_redirects` times — there is no unbounded
retry. This is safe because `not_leader` is a *pre-application* rejection: a follower refuses
a write before it enters the Raft log, so retrying it on the leader cannot double-apply it.
The helper reacts only to `not_leader`, never to ambiguous network or timeout errors. It
refuses to wrap transactions or streaming cursors (see [`CLIENT.md`](CLIENT.md)).

## Supported operations

Schema create, insert, bulk insert, find, filter, document-path filters, full-text search,
count, exists, update, delete, upsert, vector nearest, server-side cursor streaming, and
transactions. Operations a backend lacks raise `AuraBackendCapabilityError` rather than being
silently emulated.

## See also

- [BACKENDS.md](BACKENDS.md) — the backend model and DSN routing.
- [PROTOCOL.md](PROTOCOL.md) — the wire protocol.
- [ERRORS.md](ERRORS.md) — the typed error hierarchy.
- [CLIENT.md](CLIENT.md) — the reconnect and bounded-redirect helpers in detail.
- [TRANSACTIONS.md](TRANSACTIONS.md) — transaction semantics and cluster-preview rules.
- [COMPATIBILITY.md](COMPATIBILITY.md) — connector ↔ AuraDB ↔ protocol matrix.
- `examples/auradb_leader_redirect.py` — leader discovery and safe redirect, runnable.

## Search and ranking (v1.1.0)

AuraDB v1.1.0 adds BM25 ranked full-text search and hybrid text+vector retrieval. The
connector exposes these as `search_text`, `search_vector`, and `search_hybrid`; exact vector
search remains the default and correctness baseline (AuraDB v1.2.0 adds an opt-in
approximate/HNSW vector preview — in-memory/rebuilt, not production ANN). See
[SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md).
