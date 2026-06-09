# Aura Connector v0.7.0 release notes

**Paired client for AuraDB v1.3.0 (query ergonomics: group-by aggregation, approximate-vector
preview options, best-effort query profile, public ranked-search cursor resume).**

Aura Connector v0.7.0 is the matching client for the AuraDB v1.3.0 query-ergonomics release.
Every addition is **additive and backward compatible** with v0.6.x: result models parse both
old responses (new fields absent) and new responses. The Aura Wire Protocol (AWP 1) is
unchanged, and the connector stays compatible with the 1.2.x line and older servers for their
features.

> Transaction isolation remains **snapshot**. The connector does not provide or claim
> serializable isolation; `"serializable"` is accepted only as a deprecated alias mapped to
> snapshot semantics.

## Highlights

- **Group-by aggregation.** `client.query(Model).group_by("category", limit=...)` groups an
  `.aggregate()` query by a scalar field and computes its metrics per group. `await
  ....aggregate()` returns an `AggregateResult` whose `.groups` is a typed `GroupByResult`
  (a list of `AggregateGroup` entries with `key`, `count`, and per-group `metric(...)`).
  Groups are ordered count-descending then key-ascending; `group_count_total` and the
  `truncated` property tell you when a `limit` clipped the result. Works with filters and BM25
  search-facet scoping. Gated on the `group_by` capability.
- **Approximate-vector (HNSW) preview options.** `HnswOptions(m=..., ef_construction=...,
  ef_search=..., fallback="exact" | "error")` is accepted by
  `search_vector(..., approximate=HnswOptions(...))` next to the existing `approximate=True`
  and dict forms (the dict form now also accepts `fallback`). `fallback="exact"` (default)
  runs exact search when a request falls below the server's HNSW threshold; `fallback="error"`
  returns a structured error instead. Exact vector search stays the default and the
  correctness baseline. Gated on the `hnsw_preview` capability.
- **Best-effort query profile.** `query.profile()` opts a read into an advisory `QueryProfile`
  the server attaches to the result when it can — planning/execution timing, rows
  scanned/matched/returned, index/search/vector mode, facet/group counts, deadline and cursor
  mode, and warnings. Every field is optional. Surfaced on `AggregateResult.profile`. Gated on
  the `query_profile` capability when explicitly requested.
- **Public ranked-search cursor resume.** `Page[T]` (alias `SearchPage`) is the standalone,
  public result of a single ranked page fetch: `items`, `next_cursor` (opaque), `has_more`,
  and an optional `total`. `client.resume_search(search, cursor, page_size=...)` and the
  builder's `.page(page_size=..., cursor=...)` resume a ranked search from an externally-held
  token — one you can persist and reload, even in another process. Works for `search_text`
  (BM25), `search_hybrid`, exact `search_vector`, and the approximate-vector preview. This
  complements the existing in-process `search_pages(...)` loop. Gated on the `cursor_resume`
  capability.
- **Capability negotiation.** `BackendCapabilities` gains `group_by`, `query_profile`,
  `hnsw_preview`, and `cursor_resume` (default `False`). The AuraDB native and in-memory
  reference backends advertise them; the native backend gates each on what a connected server
  advertises, so an older server is reported honestly and the connector fails clearly rather
  than sending a clause the server would reject or ignore.

## Examples

Runnable examples for the new surface (smoke-tested against the in-memory reference engine):
`examples/auradb_group_by.py`, `examples/auradb_query_profile.py`,
`examples/auradb_cursor_resume.py`, and `examples/auradb_ann_preview.py`.

## Compatibility and scope

| Aura Connector | AuraDB server | Protocol | Notes |
| -------------- | ------------- | -------- | ----- |
| 0.7.x | 1.3.x | AWP 1 | Recommended. Group-by, HNSW preview options, query profile, cursor resume. |
| 0.7.x | 1.2.x / 1.1.x | AWP 1 | Supported for those servers' features; v1.3.0 features are gated off when the server does not advertise them. |
| 0.6.x | 1.3.x | AWP 1 | Supported for pre-1.3 features against a 1.3.0 server. |

## Known limitations (honest scope)

- **Not large-scale or highly-available ANN.** The approximate-vector option drives AuraDB's
  HNSW **preview** (opt-in; exact vector search remains the default and correctness baseline).
  No high-availability claim is implied.
- **The query profile is advisory.** Profile fields are best-effort: any or all may be absent,
  and an older server omits the profile entirely. Treat them as diagnostics, not a stable
  contract.
- **The cursor token is opaque and server-bounded.** The connector never parses the resume
  token; its lifetime is bounded by the server, so resume promptly. For BM25 and hybrid
  results that must stay stable under concurrent writes, run the original search and the resume
  inside one snapshot transaction.

## Upgrade

Drop-in. No API changes are required and no existing behavior changes. The new methods and
types are opt-in; if you target a pre-1.3.0 server, the new features are gated off by
capability negotiation.
