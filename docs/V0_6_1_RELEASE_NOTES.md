# Aura Connector v0.6.1 release notes

**Paired client for AuraDB v1.2.1 — conformance and documentation hardening.**

Aura Connector v0.6.1 is the matching client for the AuraDB v1.2.1 conformance and
documentation hardening release. It carries forward **all** of v0.6.0: it adds **no** API
or behavior changes, and v0.6.0 remains fully compatible. The Aura Wire Protocol (AWP 1) is
unchanged, and the connector stays compatible with the 1.1.x line and older servers for
their features.

> Transaction isolation remains **snapshot**. The connector does not provide or claim
> serializable isolation; `"serializable"` is accepted only as a deprecated alias mapped to
> snapshot semantics.

## v0.6.0 → v0.6.1

- **v0.6.0** introduced the vector and query ergonomics surface: the approximate-vector
  (HNSW) preview option on `search_vector`, the facets/aggregations API
  (`facet`/`aggregate_count`/`min`/`max` → `aggregate`), the ranked-pagination helper
  (`search_pages`), and end-to-end query timeouts (`.timeout(ms)` → `AuraTimeoutError`).
- **v0.6.1** adds no new API in that surface. It is a hardening release: it documents the
  live over-the-wire conformance coverage for those features and clarifies the honest
  backend boundaries. Its one functional change is a narrow bug fix surfaced by that new
  coverage: the AuraDB backend now forwards the per-query `timeout_ms` to the wire, so
  `QueryBuilder.timeout(ms)` is actually enforced by AuraDB v1.2.x (v0.6.0 set the deadline
  in the client IR but dropped it when translating Find / SearchPage / Aggregate reads for
  the AuraDB backend, so `.timeout(ms)` was silently ignored against an AuraDB server). The
  public API signatures are identical to v0.6.0.

## Live conformance coverage

AuraDB v1.2.1 ships connector-driven conformance scripts under
`tests/conformance/python/` that exercise **this** connector against a running AuraDB server
over the wire (never the in-memory backend):

- **Facets and aggregations** — terms facets (basic, limit, deterministic tie-break),
  `count`/`min`/`max` metrics (all and filtered), and BM25-scoped facets.
- **Ranked pagination** — duplicate-free pages by stable cursor token across BM25, hybrid,
  and exact-vector ranking, cursor-token presence, and structured invalid-cursor rejection.
- **Query timeouts** — per-query `timeout_ms` acceptance, the `query_timeout` error shape,
  and connection survival after a timeout.
- **Cluster variants** drive the same features against a leader and document follower reads
  as **eventually consistent** (never linearizable).

These run against AuraDB v1.2.x. Features that require AuraDB v1.2.x capabilities (facets,
aggregations, ranked pagination, query timeouts) raise `AuraCapabilityError` on a backend
that cannot serve them, rather than pretending to support them.

## Compatibility

- **AuraDB v1.2.1 paired** (and compatible 1.2.x). AuraDB v1.1.x and older servers remain
  compatible for their feature sets. Facets, aggregations, ranked pagination, and query
  timeouts require AuraDB v1.2.x.
- **AWP 1 unchanged.** No wire changes.
- **Default isolation remains `snapshot`.** `serializable` remains a deprecated alias to
  snapshot.
- All v0.6.0 behavior is preserved.

See [`COMPATIBILITY.md`](COMPATIBILITY.md) and [`AURADB.md`](AURADB.md).
