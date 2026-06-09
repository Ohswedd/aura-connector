# Search and ranking

Aura Connector v0.5.0 exposes AuraDB v1.1.0's search and ranking features as first-class
query-builder methods. All of them return models ordered by relevance/similarity, with
typed scores accessible via `aura.search_scores`.

## Declaring a full-text index

BM25 ranked search (`search_text`) and the text side of `search_hybrid` require a full-text
index on the model field. Declare it with `Field(full_text=True)`; the connector emits the
index in the schema it creates on the AuraDB server:

```python
class Doc(Model):
    id: int = Field(primary_key=True)
    body: str = Field(full_text=True)     # builds a BM25 full-text index
    embedding: Vector[3]
```

The legacy `text()` predicate (unranked `contains_text`) works without a declared index (the
server scans), but BM25 ranking needs the index.

## Ranked full-text search (BM25)

`search_text` performs BM25 relevance ranking over a full-text indexed field. Unlike the
legacy `text()` / `contains_text` predicate (an unranked boolean match), `search_text`
returns documents ordered by relevance.

```python
rows = await client.search(Doc).search_text("body", "vector index", rank="bm25").all()
```

| Parameter  | Default  | Meaning                                                        |
| ---------- | -------- | -------------------------------------------------------------- |
| `rank`     | `"bm25"` | `"bm25"` or `"term_frequency"`                                 |
| `operator` | `"or"`   | `"or"` (any term contributes) or `"and"` (all terms required) |
| `k1`       | server   | BM25 term-saturation parameter                                 |
| `b`        | server   | BM25 length-normalization parameter                            |
| `limit`    | none     | maximum rows to return                                         |

BM25 defaults on the server are `k1 = 1.2`, `b = 0.75`.

## Vector search (exact, and an approximate preview)

`search_vector` is exact nearest-neighbour search by default — the correctness baseline.

```python
rows = await client.search(Doc).search_vector("embedding", q, metric="cosine", top_k=10).all()
```

`metric` is `"cosine"` (default), `"euclidean"`, or `"dot"`.

### Approximate (HNSW) preview — opt-in (v0.6.0 / AuraDB v1.2.0)

Pass `approximate=True` (or a dict of HNSW parameters `m` / `ef_construction` / `ef_search`)
to opt into AuraDB v1.2.0's approximate vector **preview** for a query. Exact search remains
the default and correctness baseline; the preview trades a little recall for sub-linear query
cost. A server that does not advertise the `approximate_vector_search_preview` capability
rejects the request.

```python
# Default preview parameters:
rows = await client.search(Doc).search_vector("embedding", q, top_k=10, approximate=True).all()

# Tuned beam width (higher ef_search -> more recall, more cost):
rows = await (
    client.search(Doc)
    .search_vector("embedding", q, top_k=10, approximate={"ef_search": 128})
    .all()
)
```

Unknown or non-positive parameters raise `AuraQueryError`. **This is not large-scale ANN.**

#### `HnswOptions` and the fallback policy (v0.7.0 / AuraDB v1.3.0)

`HnswOptions` is a typed alternative to the dict form. Beyond `m` / `ef_construction` /
`ef_search`, it carries a `fallback` policy controlling what happens when an approximate
request falls below the server's HNSW threshold:

```python
from aura import HnswOptions

# fallback="exact" (default): the server runs exact search below the threshold.
opts = HnswOptions(m=16, ef_construction=200, ef_search=64, fallback="exact")
rows = await client.search(Doc).search_vector("embedding", q, top_k=10, approximate=opts).all()

# fallback="error": the server returns a structured error instead of falling back.
strict = HnswOptions(ef_search=128, fallback="error")
```

The dict form accepts `fallback` too (`approximate={"ef_search": 128, "fallback": "error"}`).
An invalid `fallback` raises `AuraQueryError`. The options take effect only on a server that
advertises the approximate-vector preview (`hnsw_preview` capability); exact search remains the
default and the correctness baseline.

## Hybrid search

`search_hybrid` fuses BM25 text relevance and exact vector similarity:

```python
rows = await (
    client.search(Doc)
    .search_hybrid(
        "body", "vector index",       # text field + query
        "embedding", q,                # vector field + query vector
        weights=(0.5, 0.5),
        fusion="weighted_sum",
        top_k=10,
    )
    .all()
)
```

Fusion modes:

- `weighted_sum` — min-max normalize each signal to `[0, 1]`, then `w_text·t + w_vector·v`.
- `reciprocal_rank_fusion` — combine `weight / (60 + rank)` over each signal's ranking.

Weights must be non-negative and not both zero. Hybrid search is single-node
production-supported; when issued through a multi-node cluster it follows AuraDB's preview
semantics.

## Result scores

```python
from aura import search_scores

for row in rows:
    s = search_scores(row)
    print(s.rank, s.score, s.text_score, s.vector_score)
```

- `score` — the primary (or fused) score.
- `text_score` / `vector_score` — component scores for hybrid results.
- `rank` — 1-based position in the ranked result set.

## Ranked pagination (v0.6.0)

`search_pages(page_size=...)` pages a ranked search by AuraDB v1.2.0's stable `search_page`
cursor tokens, instead of loading the whole result with `.all()`:

```python
async for page in client.search(Doc).search_text("body", "raft").search_pages(page_size=20):
    for row in page.rows:           # hydrated models, ranked order, stable cross-page rank
        ...
    if page.has_more:               # page.cursor is the opaque next-page token
        ...
```

Each `SearchResultPage` carries `rows`, an opaque `cursor` (the next-page token, server
issued — never construct one yourself), and `has_more`. Pagination requires a ranked clause
(`search_text` / `search_vector` / `search_hybrid`); a non-ranked builder raises
`AuraQueryError`, and a backend without the `ranked_pagination` capability raises a clear
error. Exact-vector pages are duplicate-free across concurrent writes; for duplicate-stable
BM25/hybrid paging across writes, page inside a transaction so the snapshot fixes the corpus.

## Public cursor resume (v0.7.0)

Where `search_pages(...)` drives the whole loop in process, `builder.page(...)` and
`client.resume_search(...)` fetch a **single** ranked page and return an opaque resume token an
application can persist and hand back later — even from another process (AuraDB v1.3.0):

```python
search = client.search(Doc).search_text("body", "raft")

first = await search.page(page_size=20)     # a public Page[T]
for row in first.items:
    ...
token = first.next_cursor                    # persist this opaque token if you like
# first.total is the ranked-result count when the server reports one, else None

# Later (possibly in another process), resume from the token:
page = await client.resume_search(search, token, page_size=20)
```

`Page[T]` (alias `SearchPage`) exposes `items`, `next_cursor`, `has_more`, and `total`. The
token is **opaque** — never parse it — and its lifetime is bounded by the server, so resume
promptly. Cursor resume works for BM25, hybrid, exact vector, and the approximate-vector
preview. As with `search_pages`, for stable BM25/hybrid results under concurrent writes, run
the original search and the resume inside one snapshot transaction. A backend without the
`cursor_resume` capability raises `AuraCapabilityError`.

## Capability negotiation

Search queries are checked against the backend's capabilities before execution. If a backend
does not support a requested feature, the connector raises `AuraCapabilityError` rather than
silently emulating or dropping it:

```python
caps = client.capabilities()
caps.supports("full_text_search")   # True for AuraDB native + memory
caps.supports("hybrid_search")
caps.supports("vector_search")
```

The AuraDB native backend and the in-memory reference backend implement BM25 and hybrid
search. SQL, MongoDB, and Redis backends raise a capability error for ranked-search clauses.

**Server-aware negotiation.** Against the native AuraDB backend, the connector reads the
server's advertised capabilities at handshake. If you connect to an AuraDB server that
predates v1.1.0 (no BM25 or hybrid support), `client.capabilities()` reflects that — a
`search_text` or `search_hybrid` call then raises `AuraCapabilityError` instead of sending a
clause the older server would silently ignore. `client.capabilities()` is therefore the
authoritative source for what the connected server supports; `AuraCapabilityError.context`
carries the backend name and the missing capability so callers can branch on it.

The error is also raised for unsupported backends (SQL/Mongo/Redis), and the message names
the backend — see `examples/auradb_search_capabilities.py` and
`examples/auradb_search_errors.py`.
