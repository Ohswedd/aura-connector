# Query Builder

The builder is fluent and immutable: every chained call returns a new builder wrapping
an updated AST. Builders never concatenate strings, they produce an injection-safe
Query IR.

The same builder works across every backend: the IR it produces is executed by the AuraDB
protocol, compiled to parameterized SQL by the relational backends (see
[SQL_COMPILER.md](SQL_COMPILER.md)), or translated to structured MongoDB documents. Features a
backend does not support (for example `nearest()` on SQLite, or a relational filter on Redis)
raise `AuraBackendCapabilityError` instead of being approximated — check
`client.capabilities()` or the [capability matrix](BACKEND_CAPABILITY_MATRIX.md).

## Reads

```python
# Single record
user = await client.User.find(id=1)            # awaiting a builder == .one()
user = await client.query(User).where(User.id == 1).one()
maybe = await client.query(User).where(User.id == 1).first()   # None if absent

# Many records
users = await (
    client.query(User)
    .where(User.email.contains("@acme.com"))
    .where(User.id > 100)
    .order_by(User.created_at.desc())
    .limit(25)
    .offset(50)
    .all()
)

# Aggregates
count = await client.query(User).count()
exists = await client.query(User).where(User.name == "Bob").exists()
```

`.one()` raises `AuraNotFoundError` if nothing matches and `AuraQueryError` if more than
one matches.

## Predicates

Operators on field references build predicate nodes:

```python
User.id == 1            User.id != 1
User.age < 18           User.age >= 65
User.email.contains("@")     User.name.startswith("A")
User.name.endswith("z")      User.name.like("A%")
User.id.in_([1, 2, 3])       User.id.not_in([4])
User.label.is_null()         User.label.is_not_null()
```

Combine with `&`, `|`, `~`, or the helpers `and_`, `or_`, `not_`:

```python
from aura import and_, or_
client.query(User).where(and_(User.id == 1, or_(User.name == "A", User.name == "B")))
client.query(User).where((User.id == 1) & ~(User.name == "X"))
```

## Document predicates

Index into a document/dict field with `[]`:

```python
client.query(Document).where(Document.metadata["status"] == "published")
client.query(Document).where(Document.metadata["source"].exists())
```

## Projection and relationships

```python
rows = await client.query(User).select(User.id, User.email).all()
# Accessing a non-selected field raises AttributeError (partial result).

invoice = await (
    client.query(Invoice)
    .where(Invoice.id == iid)
    .include("customer")
    .include("line_items", limit=50, order_by=[LineItem.position.asc()])
    .one()
)
```

## Vector and hybrid search

```python
matches = await (
    client.search(Document)
    .nearest(Document.embedding, query_vector, metric="cosine")   # or "euclidean", "dot"
    .where(Document.workspace == ws)
    .limit(10)
    .all()
)
for m in matches:
    print(m.title, m.__score__)

hybrid = await (
    client.search(Document)
    .text(Document.title, Document.body, query="refund policy")
    .similar_to(Document.embedding, query_vector)
    .fusion(alpha=0.65)
    .limit(20)
    .all()
)
```

## Mutations

```python
await client.insert(User(id=1, workspace=ws, email="a@b.com", name="Ann"))
await client.bulk_insert(User, rows, batch_size=1000, on_conflict="ignore")

await client.update(User).where(User.id == 1).set(name="Ann B.").execute()
await client.delete(User).where(User.id == 1).execute()

await client.upsert(User, key={User.email: "a@b.com"}, values={User.name: "Ann"})
```

## Graph traversal

```python
results = await (
    client.traverse(Friendship)
    .start(Friendship.id == 1)
    .out("friend")
    .max_depth(3)
    .limit(50)
    .all()
)
```

## Streaming

```python
async for event in client.query(Event).where(Event.ts >= since).stream(batch_size=1000):
    await process(event)
```

`.stream(batch_size=...)` drives a real **cursor/page** protocol: the client
opens a server cursor, pulls one `batch_size` page at a time, and holds **at most one
page** in memory regardless of how many rows match. `batch_size` must be positive
(`AuraQueryError` otherwise). If the consumer stops early (`break`), the open server
cursor is released and the cancellation is counted in `client.metrics.stream_cancellations`;
the client stays fully usable afterward.

> Against the in-memory reference backend the result set is materialized internally and
> handed out one bounded page at a time behind an opaque cursor token, the same contract
> a real AuraDB server cursor exposes. It is **not** true server-side cursor streaming;
> that requires a live cluster. The client-side bounded-iteration contract is real either
> way. See [`TRANSPORTS.md`](TRANSPORTS.md).

## Explain (Query IR)

Every builder can emit its client-side Query IR without executing:

```python
ir = client.query(User).where(User.id == 1).limit(10).explain()
```

## Raw escape hatch

Parameters are bound, never concatenated:

```python
rows = await client.raw("SELECT * FROM User WHERE id = $uid", {"uid": 1})
```

## Ranked search (v0.5.0)

For AuraDB v1.1.0 ranked retrieval, use the first-class search methods — see
[SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md). BM25 search needs a full-text index on the
field, declared with `Field(full_text=True)`:

```python
# BM25 ranked full-text search.
await client.search(Doc).search_text("body", "vector index", rank="bm25").all()

# Exact vector nearest-neighbour search.
await client.search(Doc).search_vector("embedding", q, metric="cosine", top_k=10).all()

# Hybrid text + vector with score fusion.
await client.search(Doc).search_hybrid("body", "vector index", "embedding", q,
                                       weights=(0.5, 0.5), fusion="weighted_sum").all()
```

Scores are read with `aura.search_scores(row)` (`score`, `text_score`, `vector_score`,
`rank`). Backends — and pre-1.1.0 AuraDB servers — that do not support a requested feature
raise `AuraCapabilityError`; `client.capabilities()` reflects the connected server. See
`examples/auradb_search_capabilities.py` and `examples/auradb_search_errors.py`.
