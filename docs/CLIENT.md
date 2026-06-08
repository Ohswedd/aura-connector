# Client

`Client` (and the `Aura` facade) is the public entry point. It owns the transport and
a model registry, executes queries, and provides transactions. It performs **no**
network IO at import time or in `__init__`.

## Connecting

```python
from aura import Aura, Client

async with Aura.connect("aura://cluster:7171/app", models=[User, Order]) as client:
    ...

# Equivalent
async with Client.connect("aura+memory://localhost/app", models=[User]) as client:
    ...
```

AuraDB/memory DSN schemes: `aura` (TCP), `auras` (TLS TCP), `aura+tcp`, `aura+memory`
and `memory` (in-memory reference server). Query parameters tune behaviour, e.g.
`?connect_timeout=5&request_timeout=30&compression=true`. Database-backend schemes
(`sqlite`, `postgres`, `mysql`/`mariadb`, `mongodb`, `redis`) select an adapter instead — see
[BACKENDS.md](BACKENDS.md). The client API is identical regardless of backend.

## Backend and capabilities

The client executes against a backend selected by the DSN scheme. Inspect it via
`client.backend` (the `Backend` instance) and `client.capabilities()` (its
`BackendCapabilities`). Calling a feature the backend does not support raises
`AuraBackendCapabilityError` rather than emulating it. `client.transport` is available only
for the protocol backends (AuraDB and memory); other backends raise a capability error if you
ask for a transport, because they speak to a database driver, not the wire protocol.

## Model registry and dynamic access

Models passed to `connect(models=[...])` are registered (along with their relationship
targets). Registered models are reachable dynamically:

```python
user = await client.User.find(id=1)        # client.<ModelName> -> QueryBuilder
user = await client.query(User).where(User.id == 1).one()
```

## Health

```python
await client.ping()        # True on a matching pong
await client.health()      # {"status": "ok", "protocol_version": 1, "transport": {...}, ...}
await client.server_schema()  # schema the server currently knows
```

## Mutations

```python
ws = await client.insert(Workspace(id=1, name="Acme"))
await client.bulk_insert(User, users, batch_size=1000, on_conflict="ignore")
await client.upsert(User, key={User.email: "a@b.com"}, values={User.name: "Ann"})
await client.update(User).where(User.id == 1).set(name="Ann").execute()
await client.delete(User).where(User.id == 1).execute()
```

## Transactions

`client.transaction()` is an async context manager: it commits on clean exit and rolls
back on exception. Mutations and queries issued through the transaction run
under its transaction id. AuraDB runs the transaction under **snapshot isolation with
optimistic conflict detection** (first-committer-wins on commit); the connector does
not upgrade it to serializable isolation. `isolation` defaults to `"snapshot"`; the
legacy `"serializable"` token is accepted only as a deprecated alias for snapshot
isolation.

```python
async with client.transaction(isolation="snapshot") as tx:
    account = await tx.query(Account).where(Account.id == aid).one()
    await tx.insert(LedgerEntry(id=1, account=account, amount_cents=-5000))
    await tx.update(Account).where(Account.id == aid).set(balance_cents=new_balance).execute()
# committed here; any exception rolls the whole transaction back

# Explicit control is also available:
tx = client.transaction()
await tx.begin()
try:
    await tx.insert(...)
    await tx.commit()
except Exception:
    await tx.rollback()
    raise
```

## Cluster leader redirection (AuraDB multi-node preview)

AuraDB's multi-node mode is experimental and opt-in; single-node mode remains the
recommended production deployment. In the preview only the leader accepts writes, and a
write to a follower raises `AuraNotLeaderError`. Two opt-in helpers handle this; neither
runs unless you call it.

- **`await client.connect_to_leader(error)`** / **`await client.reconnect_to("host:port")`**
  open a *new* client bound to the leader. The new client inherits this client's scheme,
  token authentication, and TLS settings unchanged (verification is never silently
  weakened), and carries no transaction state. `connect_to_leader` raises
  `AuraConnectionError` if the error has no usable leader address; both raise
  `AuraBackendCapabilityError` for non-AuraDB backends. A redirect that would silently drop
  TLS — an explicit plaintext address (e.g. `auradb://leader:7171`) while the client is on a
  TLS scheme — is refused unless you pass `allow_insecure=True`, and an unknown DSN scheme in
  the address is rejected outright.
- **`client.with_leader_redirect(max_redirects=1)`** returns a `LeaderRedirect` wrapper whose
  `insert` / `bulk_insert` / `upsert` / `raw` / `run(factory)` retry on the leader, bounded
  by `max_redirects` (no unbounded retry). It redirects only on a `not_leader` response with
  a usable leader address — safe because `not_leader` is a *pre-application* rejection, so
  retrying the write on the leader cannot double-apply it.

### Transaction- and stream-safe rules

The leader redirect deliberately does **not** cover transactions or streaming cursors,
because their server-side state lives on one node and cannot migrate:

- A `not_leader` raised inside `client.transaction()` propagates; it is never auto-redirected.
  Restart the whole transaction on the leader (via `connect_to_leader`), never migrate it.
- `LeaderRedirect.transaction()` raises `AuraTransactionError`, and `LeaderRedirect.stream()`
  raises `AuraBackendCapabilityError`, rather than silently doing the unsafe thing.
- Server-side transaction ids are never moved across nodes.
- Read-only operations can be resolved against the leader before execution by running them
  through `LeaderRedirect.run(...)`, because `not_leader` is reported before any rows.

## Retries

Requests are retried only when the raised error is classified retryable, and never more
than `RetryPolicy.max_attempts` times, with bounded exponential backoff. The request id
is preserved across retries.

## Errors

Server error frames are mapped to typed exceptions (`AuraNotFoundError`,
`AuraConstraintError`, `AuraValidationError`, …) carrying a stable code, retry
classification, and request id. See [ERRORS.md](ERRORS.md).

## Transport injection

Any `Transport` can be injected, which is how tests and the
[`custom_transport`](../examples/custom_transport.py) example supply deterministic
state:

```python
from aura.transport.memory import MemoryTransport, ReferenceServer

server = ReferenceServer()
client = Client(config, MemoryTransport(server), [User])
await client._open()
```
