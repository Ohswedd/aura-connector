# MySQL / MariaDB backend

A production SQL backend built on `aiomysql`. It shares the [SQL compiler](SQL_COMPILER.md)
and schema builder with the other relational backends, using the MySQL dialect: `%s`
placeholders, backtick-quoted identifiers, and `INSERT … ON DUPLICATE KEY UPDATE` upserts.

## Install and connect

```bash
pip install aura-connector[mysql]
```

```python
from aura import Aura

async with Aura.connect("mysql://root:password@localhost:3306/app", models=[Product]) as db:
    await db.insert(Product(id=1, sku="A-1"))
```

DSN schemes: `mysql://`, `mysql+aiomysql://`, `mariadb://`, `mariadb+aiomysql://`.

## What it supports

- Connect, close, ping.
- Schema create/drop, insert, bulk insert, find, filter, order, limit, offset, update,
  delete, `count`, `exists`, and `upsert`.
- Transactions, server-side cursors, and `full_text_search` (declared in capabilities).
- JSON/vector field fallback as JSON text.

## Limitations and notes

- **No `RETURNING`.** MySQL/MariaDB lack `RETURNING` in the portable subset, so inserts report
  affected-row counts rather than echoing the inserted rows; `db.insert(obj)` returns the
  object you passed in.
- Indexed string columns are created as `VARCHAR(255)`; unindexed text uses `LONGTEXT`.
- Vector search, hybrid search, and graph traversal raise `AuraBackendCapabilityError`.

## Running it locally

```bash
docker compose -f docker-compose.backends.yml up -d mysql
export AURA_TEST_MYSQL_DSN=mysql://root:password@localhost:3306/aura_test
python -m pytest tests/integration/test_mysql_backend.py -vv
```

The integration tests skip cleanly when `AURA_TEST_MYSQL_DSN` is unset. Example:
[`examples/mysql_backend.py`](../examples/mysql_backend.py).
