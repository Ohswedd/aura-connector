# SQL compiler

The SQL backends (SQLite, PostgreSQL, MySQL/MariaDB) share one compiler that translates the
connector's canonical Query IR into parameterized SQL. It is the safety-critical core of the
relational adapters, and it is written once and reused across every SQL dialect.

## Safety by construction

Two invariants hold for every statement the compiler emits:

1. **Values are bound, never interpolated.** Each user value becomes a placeholder; the value
   travels to the driver in a separate parameter list. The SQL text is fixed by the *shape* of
   the query, not by any value. A value such as `"1; DROP TABLE users; --"` is bound as a
   single string literal and cannot alter the statement.
2. **Identifiers are quoted per dialect.** Table and column names are emitted through the
   dialect's quoting (doubling the quote character, rejecting embedded NULs), so an identifier
   can never break out of its quotes.

These guarantees are enforced by dedicated tests in `tests/unit/test_sql_injection_safety.py`,
which feed known injection strings through every operation and assert the payload appears only
in the bound parameters, never in the SQL text.

## Parameter styles

Each dialect uses its native placeholder syntax:

| Dialect    | Placeholder | Example          |
| ---------- | ----------- | ---------------- |
| SQLite     | `?`         | `... WHERE "id" = ?` |
| PostgreSQL | `$1, $2, …` | `... WHERE "id" = $1` |
| MySQL      | `%s`        | `... WHERE \`id\` = %s` |

## Supported operations

The compiler covers the relational subset of the Query IR:

- **Reads:** select (with field projection), filter equality and comparisons (`=`, `<>`, `<`,
  `<=`, `>`, `>=`), `IN` / `NOT IN`, `contains` / `startswith` / `endswith` (as escaped
  `LIKE`), `like`, `IS NULL` / `IS NOT NULL`, `AND` / `OR` / `NOT`, order by, limit, offset,
  count, and exists.
- **Writes:** insert, bulk insert (one tuple per row), update, delete, and upsert. Inserts use
  `RETURNING *` where the dialect supports it (SQLite, PostgreSQL); MySQL reports affected-row
  counts. `on_conflict="ignore"` and `on_conflict="replace"` map to each dialect's idiom.
- **Schema:** `CREATE TABLE IF NOT EXISTS`, indexes, unique and primary-key constraints,
  NOT NULL, foreign-key columns for to-one relationships. See
  [MIGRATIONS.md](MIGRATIONS.md).

## Type handling and the JSON fallback

Scalars the model layer normalizes to canonical strings (`datetime`, `date`, `uuid`,
`Decimal`) are stored as text. **Lists, documents, vectors, and binary values are stored as
JSON text** — the portable JSON-field and vector-storage fallback for backends without a
native column type for them. JSON columns are decoded back into Python lists/dicts on read
using the model schema. This is honest about its limits: filtering *into* a JSON document path
is not supported on the SQL backends and raises `AuraDialectError`.

## Upsert

`db.upsert(Model, key=..., values=...)` on a SQL backend performs an update-by-key and, only
if no row matched, inserts the merged row. This portable two-step matches the connector's
upsert semantics across dialects and, unlike a single `INSERT … ON CONFLICT`, does not require
every NOT NULL column to be supplied when the target row already exists.

## Unsupported operations

Vector search, hybrid search, and graph traversal are not expressible in the SQL backends and
raise `AuraBackendCapabilityError`. Document-path filters/projection and ordering by a document
path raise `AuraDialectError`. Field types a dialect cannot map raise a clear error rather than
guessing.

## Tests

- `tests/unit/test_sql_compiler.py` — operation-by-operation SQL generation.
- `tests/unit/test_sql_dialects.py` — parameter styles, identifier quoting, type mapping.
- `tests/unit/test_sql_schema.py` — DDL generation.
- `tests/unit/test_sql_injection_safety.py` — injection strings are bound, not concatenated.
