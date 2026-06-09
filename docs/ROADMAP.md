# Aura Connector Roadmap

This roadmap tracks planned and candidate work for the Python connector. It is a
statement of direction, not a delivery commitment, and it is **not a changelog**.
Completed release history lives in [CHANGELOG.md](../CHANGELOG.md).

## Current product stance

This stance is the baseline the roadmap builds on; it is context, not a list of
deliverables.

- **Aura Connector v0.5.x is the matching connector for AuraDB v1.1.x** search and
  ranking — see [COMPATIBILITY.md](COMPATIBILITY.md).
- **The native AuraDB backend is the primary target** — see [AURADB.md](AURADB.md).
- **Non-AuraDB backends support a subset** of features and raise a structured
  `AuraCapabilityError` for unsupported search / ranking — they are never silently
  emulated. See [SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md).
- **Leader redirect is opt-in and bounded**; the connector does not auto-retry
  follower writes by default.
- **Transactions do not auto-redirect** — transaction ids are node-local and must
  be restarted on the leader.

## How to read this roadmap

- `[ ]` planned / open
- `[~]` being evaluated or under investigation
- `[x]` completed — context only, used sparingly, never as release history

Items track AuraDB server capabilities where relevant: connector search / vector
work generally lands once the AuraDB server exposes the matching server-side
feature.

## AuraDB native experience

The native backend is the primary target; ergonomics here come first.

- [ ] More ergonomic schema / index declaration for search fields.
- [ ] Better capability-introspection helpers around `client.capabilities()`.
- [ ] More `EXPLAIN ANALYZE` helpers.
- [ ] Richer typed score / result models.
- [ ] Connection-profile helpers for auth and TLS.

## Search and ranking APIs

BM25, hybrid, and vector search APIs ship today; these refine them and track new
AuraDB server features.

- [ ] Search pagination helpers.
- [~] Highlight / snippet support — only if AuraDB adds it server-side.
- [~] Facet / aggregation APIs — only if AuraDB adds them server-side.
- [ ] Search relevance evaluation examples.
- [ ] Hybrid ranking presets.
- [ ] Better validation messages for search options.

## Vector APIs

Exact vector search ships today and remains the default and correctness baseline.

- [x] ANN preview API — `search_vector(..., approximate=True)` opts into AuraDB v1.2.0's
  opt-in HNSW preview (shipped in v0.6.0); not production ANN.
- [ ] Exact-vs-approximate comparison helpers.
- [ ] Batch vector-query helpers.
- [ ] Vector validation utilities.

## Backend adapters

Adapters make the same typed API useful with existing databases; they support a
subset of AuraDB features and report the rest honestly.

- [ ] Clearer capability matrix per backend.
- [~] More SQL-backend parity where feasible.
- [ ] Better unsupported-feature error messages.
- [ ] Optional backend integration test matrix.
- [ ] Keep honest capability errors — do not pretend non-AuraDB backends support
  AuraDB-native search.

## Transactions and leader routing

Leader redirect is opt-in and bounded; transactions are node-local. This category
improves the helpers around that model without weakening its safety.

- [ ] More leader-discovery helpers.
- [ ] Better retry-policy configuration.
- [ ] More transaction-safety examples.
- [ ] Clearer cluster-read guidance.

## Developer experience

- [ ] Better type-checking examples.
- [ ] More framework integration examples.
- [ ] Improved error documentation.
- [ ] Better logging hooks.
- [ ] Async lifecycle helpers.

## Testing and release engineering

- [ ] More live AuraDB conformance scenarios.
- [ ] Search / ranking regression fixtures.
- [ ] Backend-matrix CI improvements.
- [ ] Packaging-metadata checks.
- [ ] Executable docs examples.

## Not currently planned for immediate work

These are listed so the boundary is explicit. They are not promised and not
implied by any other section.

- Making all backends support every AuraDB feature.
- Unbounded automatic cluster retries.
- Hiding AuraDB capability errors.
- Replacing AuraDB server-side semantics inside the connector.
