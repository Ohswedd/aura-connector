# Aura Connector v0.5.0 release notes

Coordinated release with **AuraDB v1.1.0**. This release adds first-class connector support
for AuraDB's new search and ranking features: BM25 ranked full-text search, hybrid
text-plus-vector retrieval, typed result scores, and capability negotiation.

## Scope and compatibility

- **Protocol:** Aura Wire Protocol **AWP 1** is unchanged. The new search clauses are
  additive Query IR fields and additive response fields; older servers simply ignore them.
- **AuraDB pairing:** tested against AuraDB **1.1.0**, supported across **1.1.x**.
- **Production stance:** AuraDB single-node is the recommended production deployment.
  Multi-node remains an HA candidate preview, not production high availability. This
  connector does not add production HA, automatic failover, or distributed transactions.
- **Vector search:** exact vector search remains the correctness baseline. Approximate
  (ANN/HNSW) vector search is **not** implemented in AuraDB v1.1.0.

## New APIs

### Ranked full-text (BM25)

```python
rows = await client.search(Doc).search_text("body", "raft consensus", rank="bm25").all()
```

- `rank` is `"bm25"` (default) or `"term_frequency"`.
- `operator` is `"or"` (default; any term contributes) or `"and"` (every term required).
- `k1` and `b` tune BM25 term saturation and length normalization.

### Exact vector search

```python
rows = await client.search(Doc).search_vector("embedding", q, metric="cosine", top_k=10).all()
```

### Hybrid search

```python
rows = await (
    client.search(Doc)
    .search_hybrid("body", "raft", "embedding", q, weights=(0.5, 0.5), fusion="weighted_sum")
    .all()
)
```

- `fusion` is `"weighted_sum"` (min-max normalized) or `"reciprocal_rank_fusion"`.
- `weights` is `(text_weight, vector_weight)`; both must be non-negative and not both zero.

### Typed result scores

```python
from aura import search_scores

s = search_scores(rows[0])
s.score          # fused / primary relevance score
s.text_score     # BM25 component (hybrid)
s.vector_score   # vector-similarity component (hybrid)
s.rank           # 1-based rank in the result set
```

### Capability negotiation

Ranked-search queries are checked against the backend's advertised capabilities. A backend
that does not support a requested feature raises `AuraCapabilityError` (an alias of
`AuraBackendCapabilityError`) instead of silently dropping the clause:

```python
if client.capabilities().supports("hybrid_search"):
    ...
```

The AuraDB native backend and the in-memory reference backend support BM25 and hybrid
search. SQL/Mongo/Redis backends raise a capability error for these clauses, and the error
names the backend.

**Server-aware negotiation.** Against the native AuraDB backend, the connector reads the
server's advertised capabilities at handshake, so `client.capabilities()` reflects the
connected server. A `search_text`/`search_hybrid` call against a pre-1.1.0 AuraDB server
(which does not advertise BM25/hybrid) raises `AuraCapabilityError` rather than sending a
clause the older server would silently ignore.

### Declaring a full-text index

BM25 search requires a full-text index on the model field; declare it with
`Field(full_text=True)`, which the connector emits in the AuraDB schema it creates. The
legacy `text()` predicate still works without a declared index.

## Examples

- `examples/auradb_text_search.py`
- `examples/auradb_hybrid_search.py`
- `examples/auradb_explain_analyze.py`
- `examples/auradb_search_capabilities.py`
- `examples/auradb_search_errors.py`

## Upgrading from 0.4.1

No breaking changes. Existing `nearest`, `similar_to`, `text`, and `fusion` methods are
unchanged. Add the new `search_*` methods where you want ranked retrieval.
