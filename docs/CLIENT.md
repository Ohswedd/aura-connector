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

DSN schemes: `aura` (TCP), `auras` (TLS TCP), `aura+tcp`, `aura+memory`
(in-memory reference server). Query parameters tune behaviour, e.g.
`?connect_timeout=5&request_timeout=30&compression=true`.

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
under its transaction id.

```python
async with client.transaction(isolation="serializable") as tx:
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
