# Native AuraDB backend

The **native AuraDB backend** speaks the Aura Wire Protocol version 1 (AWP 1) to a running
[AuraDB](https://github.com/Ohswedd/auradb) server over TCP or TLS. It opens its own socket,
performs the AWP handshake (with optional static-token authentication), translates the
connector's canonical Query IR to the server's Query IR, and decodes results back into your
typed models. AWP 1 with auth and TLS first shipped in AuraDB v0.2.0, so **v0.2.0 is the
minimum native server version**; the current coordinated server is AuraDB v1.4.0 (see
[COMPATIBILITY.md](COMPATIBILITY.md)).

This connector release (v0.8.0) is the paired client for AuraDB v1.4.0. It carries forward the
full v0.7.x query surface — group-by aggregation (`group_by`), approximate-vector (HNSW)
preview options including a `fallback` policy (`HnswOptions`), a best-effort query profile
(`profile()` / `QueryProfile`), and public ranked-search cursor resume (`Page`/`SearchPage`,
`client.resume_search`, `builder.page`) — each still gated on a capability flag (`group_by`,
`query_profile`, `hnsw_preview`, `cursor_resume`) the native backend negotiates at handshake;
a server that does not advertise a capability gets an `AuraCapabilityError` rather than a
silently-wrong result. v0.8.0 adds purely **client-side ergonomics with no wire-protocol
change**: connection profiles (`ConnectionProfile`, `from_env`, `Client.from_profile` /
`Aura.from_profile`, TLS CA and SNI wiring, token-redacting `repr`), search-eval report parsing
helpers (`SearchEvalReport` / `SearchEvalMetrics` / `SearchEvalQueryResult` and BM25/hybrid
report models, which parse `auradb search eval` CLI output and do not run server-side CLI
commands), and capability require/describe helpers. The default transaction isolation remains
`snapshot`.

The same typed model and query API you use with every other backend works unchanged — only
the DSN changes.

> **Coordinated v1.4.0 line.** AuraDB v1.4.0 is a production operability and search-quality
> release: server-side single-node operability drills and recovery confidence (backup, verify,
> restore-to-fresh, snapshot rollback, disk/I/O drills) plus the `auradb search eval` relevance
> evaluation toolchain. That work is purely operational/evaluation tooling and requires **no**
> connector API or protocol change — a v0.7.x connector keeps working unchanged against AuraDB
> v1.4.0. The paired connector v0.8.0 adds the client-side ergonomics described above. Single-node
> remains the production-supported mode; there is no production HA or production ANN claim.

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

## Connection profiles (v0.8.0)

A `ConnectionProfile` is a small, immutable convenience layer over a DSN plus the operational
knobs deployments usually read from the environment. It resolves through the **same**
`parse_dsn` path as every other entry point, so it adds no new connection behaviour — only a
typed, redacted, env-friendly way to assemble the inputs.

```python
from aura import Aura, ConnectionProfile

# Reads AURA_ADDR (required) plus the optional AURA_AUTH_TOKEN, AURA_TLS_CA,
# AURA_SERVER_NAME, AURA_CONNECT_TIMEOUT_MS, AURA_REQUEST_TIMEOUT_MS, AURA_ISOLATION.
profile = ConnectionProfile.from_env(prefix="AURA")
async with Aura.from_profile(profile, models=[User]) as client:
    ...
```

| Env var (with `AURA` prefix) | Field | Notes |
| ---------------------------- | ----- | ----- |
| `AURA_ADDR` | `addr` | **required** — an `aura://` / `auras://` DSN |
| `AURA_AUTH_TOKEN` | `auth_token` | bearer token; redacted from `repr` |
| `AURA_TLS_CA` | `tls_ca` | CA bundle path; validated to exist |
| `AURA_SERVER_NAME` | `server_name` | TLS SNI override (see below) |
| `AURA_CONNECT_TIMEOUT_MS` | `connect_timeout_ms` | positive integer milliseconds |
| `AURA_REQUEST_TIMEOUT_MS` | `request_timeout_ms` | positive integer milliseconds |
| `AURA_ISOLATION` | `isolation` | default isolation token |

- **Not a secret manager.** The auth token is whatever your deployment's secret management
  already placed in the environment. The profile redacts it from `repr` so it does not leak
  into logs, but storing, rotating, and protecting that secret is the deployment's job.
- **Safe defaults.** Timeouts default to 10s / 30s; `isolation` defaults to `snapshot`. The
  deprecated `serializable` token normalizes to `snapshot` (AuraDB is not serializable, and
  the connector does not upgrade it).
- **Validation.** A missing address, a non-integer or non-positive timeout, or a TLS CA path
  that does not exist raises `AuraValidationError` with a clear, secret-free message.
- **SNI server name.** `server_name` is carried as the `tls_server_name` transport option; the
  bundled TCP transport uses it as the TLS SNI server name. When unset, SNI is derived from the
  address host as before.
- **Inspect without connecting.** `profile.to_client_config()` returns the fully-resolved
  `ClientConfig`. `ConnectionProfile` is a convenience helper, not a new client API — existing
  `connect(...)` / `Client(...)` constructors are unchanged.

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
approximate/HNSW vector preview — in-memory/rebuilt, not large-scale ANN). AuraDB v1.3.0 adds
`HnswOptions` (HNSW preview parameters plus an `"exact"`/`"error"` fallback policy) and public
cursor resume (`Page`/`SearchPage`, `client.resume_search`, `builder.page`). See
[SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md).

## Aggregations, group-by, and query profile (v1.2.0 / v1.3.0)

`client.query(Model).facet(...).aggregate_count().min(...).max(...).aggregate()` returns a
typed `AggregateResult` (AuraDB v1.2.0). AuraDB v1.3.0 adds group-by aggregation
(`group_by(field, limit=...)`, surfaced as `AggregateResult.groups` / `GroupByResult`) and a
best-effort query profile (`profile()`, surfaced as `AggregateResult.profile` / `QueryProfile`;
every field is advisory and optional). Each is gated on the `group_by` / `query_profile`
capability. See [QUERY_BUILDER.md](QUERY_BUILDER.md).

## Capability UX (v0.8.0)

`client.capabilities()` returns the backend's honest `BackendCapabilities` declaration. v0.8.0
adds two ergonomics helpers alongside the existing `supports(...)`:

```python
caps = client.capabilities()

if caps.supports("query_profile"):     # soft branch
    ...

caps.require("group_by")               # hard requirement; raises if missing

summary = caps.describe()              # {"name", "supported", "unsupported"}
```

- **`supports(flag)`** returns a bool; an unknown flag name raises `ValueError` (it is a
  programmer error, not a missing feature).
- **`require(flag)`** raises `AuraBackendCapabilityError` when a known capability is not
  provided, naming the backend and the missing capability in its `context` so callers branch on
  capabilities instead of catching a generic failure.
- **`describe()`** returns the backend name plus `supported` / `unsupported` lists (each a
  subset of the capability flags, in declaration order) for display.

These read the **current** capability payload and require no server change. A non-AuraDB
backend will not claim AuraDB-specific analytics — e.g. a key/value backend reports
`query_profile` and `group_by` as unsupported and `require("hybrid_search")` raises. See
[COMPATIBILITY.md](COMPATIBILITY.md) and [BACKEND_CAPABILITY_MATRIX.md](BACKEND_CAPABILITY_MATRIX.md).
