# SQLite backend

SQLite is Aura's first-class local backend. It is fully functional with **no external
service**, which makes it the easiest way to try the connector and the default target for the
integration test suite.

## Install and connect

```bash
pip install aura-connector[sqlite]
```

```python
from aura import Aura, Field, Model

class Task(Model):
    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    done: bool = Field(default=False)
    tags: list[str] = Field(default_factory=list)

# File-backed:
async with Aura.connect("sqlite:///var/app.db", models=[Task]) as db:
    await db.insert(Task(id=1, title="write docs"))

# In-memory (fresh database per connection):
async with Aura.connect("sqlite://", models=[Task]) as db:
    ...
```

DSN schemes: `sqlite://` and `sqlite+aiosqlite://`. A path (`sqlite:///path/to.db`) selects a
file; an empty path selects an in-memory database. The driver is `aiosqlite`.

## What it supports

- Connect, close, ping.
- Schema create/drop (idempotent `CREATE TABLE IF NOT EXISTS`) on connect.
- Insert, bulk insert, find, filter, order, limit, offset, update, delete.
- `count`, `exists`, and `upsert` (update-by-key, else insert).
- Transactions (`async with db.transaction()`), with commit on success and rollback on error.
- JSON field fallback: list and document fields are stored as JSON text and decoded on read.
- Vector storage fallback: vectors are stored as JSON arrays. (Vector *search* is not
  supported — see below.)
- Streaming via paged `LIMIT`/`OFFSET` windows, holding at most one page in memory.
- Local migration diffing (`db.backend.diff_schema([...])`).

## What it does not support

SQLite has no vector index, full-text-by-default, or graph engine in this connector, so the
following raise `AuraBackendCapabilityError`:

- Vector search (`nearest`/`similar_to`) and hybrid search.
- Graph traversal (`db.traverse(...)`).

These are declared `False` in the [capability matrix](BACKEND_CAPABILITY_MATRIX.md); branch on
`db.capabilities()` if a code path is shared with AuraDB.

## Notes

- Transactions are connection-scoped: run them sequentially on a single client.
- Identifiers are double-quoted; all values are bound parameters (`?`). See
  [SQL_COMPILER.md](SQL_COMPILER.md).
- Example: [`examples/sqlite_backend.py`](../examples/sqlite_backend.py).
