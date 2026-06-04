# PostgreSQL backend

A production SQL backend built on `asyncpg`. It shares the [SQL compiler](SQL_COMPILER.md) and
schema builder with the other relational backends, using the PostgreSQL dialect: `$1`
placeholders, double-quoted identifiers, `RETURNING`, and `INSERT … ON CONFLICT` semantics.

## Install and connect

```bash
pip install aura-connector[postgres]
```

```python
from aura import Aura

async with Aura.connect(
    "postgresql://user:pass@localhost:5432/app", models=[User]
) as db:
    await db.insert(User(id=1, name="Ada"))
```

DSN schemes: `postgres://`, `postgresql://`, `postgresql+asyncpg://`. Credentials, host, port,
and database are taken from the DSN; the password is never included in the client config repr.

## What it supports

- Connect, close, ping.
- Schema create/drop, inserts (with `RETURNING`), bulk insert, find, filter, order, limit,
  offset, update, delete, `count`, `exists`, and `upsert`.
- Transactions, server-side cursors, and `full_text_search` (declared in capabilities).
- JSON/vector field fallback as JSON text (see [SQL_COMPILER.md](SQL_COMPILER.md)).

## What it does not support

Vector search, hybrid search, and graph traversal raise `AuraBackendCapabilityError`. (Native
`pgvector` is not wired in this release; the connector does not claim a capability it does not
implement.)

## Running it locally

```bash
docker compose -f docker-compose.backends.yml up -d postgres
export AURA_TEST_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/aura_test
python -m pytest tests/integration/test_postgres_backend.py -vv
```

The integration tests skip cleanly when `AURA_TEST_POSTGRES_DSN` is unset. Example:
[`examples/postgres_backend.py`](../examples/postgres_backend.py).
