# MongoDB backend

A document-native backend built on `motor`. Queries compile to **structured MongoDB filter
and update documents** — never to generated query strings — so user values are always data,
never code.

## Install and connect

```bash
pip install aura-connector[mongodb]
```

```python
from aura import Aura

async with Aura.connect("mongodb://localhost:27017/app", models=[Article]) as db:
    await db.insert(Article(id=1, title="Welcome", tags=["intro"]))
```

DSN schemes: `mongodb://`, `mongodb+motor://`. The database name comes from the DSN path.

## What it supports

- Connect, close, ping.
- Collections and indexes created from your models (`unique`/`index` fields become indexes).
- Insert, bulk insert, find, equality and comparison filters, `IN`, `AND`/`OR`/`NOT`,
  `contains`/`startswith`/`endswith` (as anchored regexes), order, limit, offset (skip),
  update, delete, `count`, `exists`, and `upsert`.
- Lists and documents stored natively; vectors stored as arrays.
- Streaming via a native cursor with `batch_size`.

## Honest limitations

- **Transactions** require a replica set, so `transactions` is declared `False` for the
  standalone default; `db.transaction()` would raise a capability error. Enable it for your
  deployment if your cluster supports multi-document transactions.
- **Vector search** requires a configured vector index (for example Atlas `$vectorSearch`), so
  `vector_search` is declared `False` and `nearest`/`similar_to` raise a capability error
  rather than scanning.
- **Graph traversal** and **hybrid/full-text search** are not provided and raise a capability
  error.
- To-one relationships are stored as the target's primary key.

## Running it locally

```bash
docker compose -f docker-compose.backends.yml up -d mongodb
export AURA_TEST_MONGODB_DSN=mongodb://localhost:27017/aura_test
python -m pytest tests/integration/test_mongodb_backend.py -vv
```

The integration tests skip cleanly when `AURA_TEST_MONGODB_DSN` is unset. Example:
[`examples/mongodb_backend.py`](../examples/mongodb_backend.py).
