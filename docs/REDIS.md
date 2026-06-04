# Redis backend (limited)

Redis is a key-value store, not a relational or document database, and this backend is
**intentionally limited** to what Redis does well: caching and primary-key document access.
It is built on `redis.asyncio`.

## Install and connect

```bash
pip install aura-connector[redis]
```

```python
from aura import Aura, Field, Model

class Session(Model):
    id: str = Field(primary_key=True)
    user: str
    data: dict[str, str] = Field(default_factory=dict)

async with Aura.connect("redis://localhost:6379/0", models=[Session]) as db:
    await db.insert(Session(id="s1", user="ada", data={"theme": "dark"}))
    s = await db.Session.find(id="s1")
```

DSN schemes: `redis://`, `redis+asyncio://`. Each model instance is stored as a JSON value
under the key `<Model>:<primary_key>`.

## What it supports

- Connect, close, ping.
- Insert / upsert as a key-value document keyed by primary key.
- Find by primary key, delete by primary key, existence checks.
- `count` and listing via a prefix scan over `<Model>:*`.
- Schema "creation" as a no-op metadata validation step (it requires a primary key).
- Streaming via prefix scan.

## What it does not support (by design)

These raise `AuraBackendCapabilityError`:

- Relational filtering (anything other than a primary-key equality), joins, and ordering on
  non-key fields.
- Graph traversal.
- Vector search and hybrid/full-text search.

Capabilities declare `key_value = True` and `relationships = False`; treat Redis as a cache or
key-value layer, not a query engine.

## Running it locally

```bash
docker compose -f docker-compose.backends.yml up -d redis
export AURA_TEST_REDIS_DSN=redis://localhost:6379/0
python -m pytest tests/integration/test_redis_backend.py -vv
```

The integration tests skip cleanly when `AURA_TEST_REDIS_DSN` is unset. Example:
[`examples/redis_backend.py`](../examples/redis_backend.py).
