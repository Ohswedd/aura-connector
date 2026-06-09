# Aura Connector v0.6.0 release notes

**Paired client for AuraDB v1.2.0 (query ergonomics: aggregations, terms facets, cooperative
query timeouts).**

Aura Connector v0.6.0 is the matching client for the AuraDB v1.2.0 query-ergonomics release.
The Aura Wire Protocol (AWP 1) is unchanged, and the connector stays compatible with the
1.1.x line and older servers for their features.

> Transaction isolation remains **snapshot**. The connector does not provide or claim
> serializable isolation; `"serializable"` is accepted only as a deprecated alias mapped to
> snapshot semantics.

## Highlights

- **Approximate-vector (HNSW) preview option.** `search_vector(..., approximate=True)` opts
  a query into AuraDB v1.2.0's HNSW preview (tunable via `m`/`ef_construction`/`ef_search`);
  exact search stays the default and correctness baseline. Not production ANN.
- **Facets and aggregations.** `client.query(Model).facet("category").aggregate_count().min("price").max("price")`
  then `await ....aggregate()` returns a typed `AggregateResult` with `metric(...)` / `facet(...)`
  lookups. Supports filters and BM25 search-facet scoping. Validated end-to-end against the
  in-memory reference engine.
- **Ranked pagination helper.** `client.search(Model).search_text(...).search_pages(page_size=...)`
  pages a ranked search by AuraDB v1.2.0's stable `search_page` cursor tokens, yielding
  `SearchResultPage` objects (`rows`, `cursor`, `has_more`). Works for `search_text`,
  `search_vector`, and `search_hybrid`; non-ranked queries and capability-lacking backends
  raise a clear error. Tokens are opaque and server-issued. Validated end-to-end against the
  in-memory reference engine.
- **Query timeouts honored end-to-end.** The existing `.timeout(milliseconds)` query option
  emits `timeout_ms`, which AuraDB v1.2.0 now enforces. A read that exceeds the deadline
  returns a structured `query_timeout` error.
- **`query_timeout` → `AuraTimeoutError`.** The native backend maps the new `query_timeout`
  code (and `transaction_timeout`) to `AuraTimeoutError`, preserving the wire code, so a
  deadline cancellation is distinguishable from a malformed query. The connection stays
  usable afterward.

## Examples

Runnable examples for the new surface (smoke-tested against the in-memory reference engine):
`examples/auradb_facets.py`, `examples/auradb_search_pagination.py`,
`examples/auradb_query_timeout.py`, and `examples/auradb_vector_options.py` (exact + the
approximate preview).

## Compatibility and scope

| Aura Connector | AuraDB server | Protocol | Notes |
| -------------- | ------------- | -------- | ----- |
| 0.6.x | 1.2.x | AWP 1 | Recommended. `query_timeout` mapping; `.timeout()` enforced. |
| 0.6.x | 1.1.x / 1.0.x | AWP 1 | Supported for those servers' features; `timeout_ms` is ignored by pre-1.2.0 servers (no error). |
| 0.5.x | 1.2.x | AWP 1 | Supported for pre-1.2 features against a 1.2.0 server. |

## Not in this release (honest scope)

- Production-grade ANN is not implied — the approximate-vector option drives AuraDB v1.2.0's
  HNSW **preview** (opt-in; exact vector search remains the default and correctness baseline).

## Upgrade

Drop-in. No API changes are required. `.timeout(ms)` now has an effect against AuraDB
v1.2.0; if you relied on long-running reads, confirm the server's `max_query_time_ms`
default (30s) suits your workload or set a per-query timeout.
