# Vectors

`Vector[N]` is a first-class, fixed-dimension vector type usable in model annotations
and as a runtime value.

```python
from aura import Model, Field, Vector

class Document(Model):
    id: int = Field(primary_key=True)
    embedding: Vector[1536] = Field(vector_index="hnsw")
```

## Construction and validation

```python
v = Vector[3]([1.0, 0.0, 0.5])      # dimension-checked
Vector[3]([1.0, 0.0])               # AuraValidationError: wrong dimension
Vector([float("nan")])              # AuraValidationError: non-finite
```

- Components are coerced to `float` and must be finite.
- `Vector[N]` produces a cached, dimension-bound subclass: `Vector[8] is Vector[8]`.
- A bare `Vector([...])` accepts any length unless `dim=` is passed.

Useful methods: `to_list()` / `tolist()`, `dimension`, `magnitude()`, plus full
`Sequence[float]` behaviour (`len`, indexing, iteration, equality).

When you assign a `list[float]` to a vector field, Aura coerces and validates it:

```python
doc = Document(id=1, embedding=[0.1, 0.2, ...])   # becomes a Vector[1536]
```

## Similarity search

The client expresses vector queries; the server performs the actual nearest-neighbour search. Exact search is the default and correctness baseline; against AuraDB v1.2.0 a query can opt into the approximate (HNSW) preview (not production ANN). Supported metrics: `cosine`, `euclidean`, `dot`.

```python
matches = await (
    client.search(Document)
    .nearest(Document.embedding, query_vector, metric="cosine")
    .limit(10)
    .all()
)
for m in matches:
    print(m.title, m.__score__)   # score metadata attached during hydration
```

Field-level distance expressions are also available for advanced predicates:

```python
Document.embedding.cosine_distance(query_vector)
Document.embedding.l2_distance(query_vector)
Document.embedding.dot_product(query_vector)
```

## Memory behaviour (honest note)

A vector is stored as a `list[float]` and serialized to a JSON array inside the protocol
payload; there is no zero-copy guarantee in either the pure-Python or native path.

## Binary packing and native acceleration

`Vector.pack()` produces a contiguous little-endian IEEE-754 **f32** buffer
(`4 * dimension` bytes); `Vector.from_bytes(data, dimension)` is its inverse. Fixed-
dimension validation and this packing run through the `aura._native` adapter, which uses
the optional compiled `aura_native` extension when installed and an identical pure-Python
implementation otherwise (the outputs are byte-for-byte identical). f32 narrowing from
Python's f64 is intentional, so `from_bytes(v.pack(), v.dimension)` round-trips at f32
precision. Native acceleration is enabled via `pip install aura-connector[native]` and is
a transparent speed path only, it never changes the public API. See
[`NATIVE_ACCELERATION.md`](NATIVE_ACCELERATION.md).
