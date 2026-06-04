# Backend capability matrix

Every backend declares exactly what it supports through a `BackendCapabilities` value. Aura
reads these to decide whether an operation can run, and raises `AuraBackendCapabilityError`
when a feature is not supported — it never emulates a missing capability. Inspect the live
values with `db.capabilities()` (or `db.backend.capabilities()`).

The table below is the authoritative declaration shipped with this release.

| Capability | AuraDB | Memory | SQLite | PostgreSQL | MySQL | MongoDB | Redis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `transactions` | yes | yes | yes | yes | yes | no | no |
| `schema_create` | yes | yes | yes | yes | yes | yes | yes |
| `schema_migrations` | yes | yes | yes | yes | yes | no | no |
| `json_fields` | yes | yes | yes | yes | yes | yes | yes |
| `relationships` | yes | yes | yes | yes | yes | yes | no |
| `graph_traversal` | yes | yes | no | no | no | no | no |
| `vector_search` | yes | yes | no | no | no | no | no |
| `hybrid_search` | yes | yes | no | no | no | no | no |
| `full_text_search` | yes | yes | no | yes | yes | no | no |
| `raw_queries` | yes | yes | yes | yes | yes | no | no |
| `explain` | yes | yes | yes | yes | yes | yes | no |
| `server_side_cursors` | yes | yes | no | yes | yes | yes | no |
| `native_protocol` | yes | yes | no | no | no | no | no |
| `document_queries` | yes | yes | no | no | no | yes | no |
| `key_value` | no | no | no | no | no | no | yes |

## How to read this

- **AuraDB** is the native, high-performance target and the only backend declaring
  `native_protocol`. The **Memory** backend runs the same protocol path in-process for tests
  and examples.
- **SQL backends** (SQLite, PostgreSQL, MySQL/MariaDB) cover the full relational CRUD,
  filter, order, paginate, count/exists, upsert, transaction, and schema surface. They store
  list/document/vector fields as JSON text (the `json_fields` fallback) and do **not** provide
  vector, hybrid, or graph features. PostgreSQL and MySQL expose `full_text_search` and
  server-side cursors; SQLite streams via paged queries.
- **MongoDB** is document-native. `transactions` are declared `no` because they require a
  replica set, and `vector_search` is declared `no` because it requires a configured vector
  index — both raise a capability error here rather than being assumed. Enable them in your
  own deployment-specific subclass if your cluster provides them.
- **Redis** is a limited key-value/cache backend: primary-key document access, existence,
  prefix counts, and key-value upsert only. Relational filters, joins, graph traversal, and
  vector search are unsupported and raise `AuraBackendCapabilityError`.

## Capability-aware code

```python
async with Aura.connect(dsn, models=[Doc]) as db:
    caps = db.capabilities()
    if caps.vector_search:
        hits = await db.search(Doc).nearest(Doc.embedding, query).limit(10).all()
    else:
        hits = await db.query(Doc).where(Doc.title.contains(term)).limit(10).all()
```

Catching the error is equally valid when a feature is optional:

```python
from aura import AuraBackendCapabilityError

try:
    await db.search(Doc).nearest(Doc.embedding, query).all()
except AuraBackendCapabilityError as exc:
    # exc.context["capability"] names the missing feature, e.g. "vector_search"
    ...
```
