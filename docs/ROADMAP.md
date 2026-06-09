# Aura Connector Roadmap

This roadmap tracks planned and candidate work for the Python connector. It is a
statement of direction, not a delivery commitment, and it is **not a changelog**.
Completed release history lives in [CHANGELOG.md](../CHANGELOG.md).

## Current stable baseline

The roadmap is written against the latest shipped release.

- **Aura Connector v0.7.0** is the current stable release; it is tested with
  **AuraDB v1.3.x** — see [COMPATIBILITY.md](COMPATIBILITY.md).
- **`DEFAULT_ISOLATION` is `snapshot`.** The connector does not provide or claim
  serializable isolation.
- **`"serializable"` is only a deprecated compatibility alias** mapped to snapshot
  semantics; it is not a distinct isolation level.

## Current product stance

This stance is the baseline the roadmap builds on; it is context, not a list of
deliverables.

- **Aura Connector v0.7.0 is the matching connector for AuraDB v1.3.x** query
  ergonomics, search, and ranking — see [COMPATIBILITY.md](COMPATIBILITY.md).
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

## Target: Aura Connector v0.8.0 — Production operability and search quality

The next big release pairs with **AuraDB v1.4.0**. It focuses on **ergonomic
production single-node connection workflows**, **search and vector quality helpers**,
and **clearer capability UX** — not on production HA automation, unbounded
retry/failover, all-backend parity for AuraDB-only analytics, or production ANN
claims (those stay in "Later / not v0.8.0 by default" and "Not currently planned").
`DEFAULT_ISOLATION` remains `snapshot`.

Planned scope (detailed per category in the sections below):

- **Ergonomic production connection profiles:** auth/TLS examples, environment-based
  profiles, and safer error messages.
- **Search quality helpers:** a relevance evaluation helper, a hybrid ranking
  calibration helper, an exact-vs-ANN comparison helper, and result-scoring
  diagnostics.
- **Typed operational models:** SLO/report model helpers where useful, richer query
  profile wrappers, and restore/backup smoke helpers where appropriate.
- **Docs / examples:** executable examples for production single-node
  auth/TLS/search/vector workflows, unsupported-feature examples, and a v1.4
  compatibility matrix.
- **Capability UX:** clearer feature-negotiation output and better unsupported
  backend guidance.

The category sections below carry the per-item tracking; this section is the
v0.8.0 lens over them.

## AuraDB native experience

The native backend is the primary target; ergonomics here come first.

- [x] Capability negotiation for AuraDB v1.3 features — shipped through v0.7.0:
  `group_by`, `query_profile`, `hnsw_preview`, and `cursor_resume` flags on
  `BackendCapabilities`, gated on what a connected server advertises.
- [x] Best-effort query profile helper — shipped in v0.7.0 (`query.profile()` and the
  typed `QueryProfile` on `AggregateResult.profile`; advisory, every field optional).
- [x] Typed result models — shipped through v0.7.0: `GroupByResult`, `AggregateGroup`,
  `QueryProfile`, and `Page[T]` (alias `SearchPage`) join the earlier facet/aggregate
  models.
- [ ] More ergonomic schema / index declaration for search fields.
- [ ] Better capability-introspection helpers around `client.capabilities()` —
  clearer feature-negotiation output (v0.8.0).
- [ ] More `EXPLAIN ANALYZE` / query-profile wrapper helpers.
- [ ] Richer typed score / result models.
- [ ] Connection-profile helpers for auth and TLS (v0.8.0).

## Search and ranking APIs

BM25, hybrid, and vector search APIs ship today; these refine them and track new
AuraDB server features.

- [x] Facet / aggregation APIs — shipped: `QueryBuilder.facet` / `.aggregate`
  (terms facets and `count`/`min`/`max`/`avg` metrics), gated on the AuraDB server
  capability.
- [x] Group-by analytics API — shipped in v0.7.0 (`query.group_by(field, limit=...)`
  with the typed `GroupByResult` / `AggregateGroup` on `AggregateResult.groups`),
  gated on the backend's `group_by` capability.
- [x] Ranked pagination + public cursor resume — shipped: `QueryBuilder.search_pages()`
  plus v0.7.0's public `Page[T]`, `client.resume_search(...)`, and `builder.page(...)`
  returning an opaque, persistable resume token (gated on `cursor_resume`).
- [~] Highlight / snippet support — only if AuraDB adds it server-side.
- [ ] Search relevance evaluation helper (v0.8.0).
- [ ] Hybrid ranking presets / calibration helper (v0.8.0).
- [ ] Result-scoring diagnostics (v0.8.0).
- [ ] Better validation messages for search options.

## Vector APIs

Exact vector search ships today and remains the default and correctness baseline.

- [x] ANN preview API — `search_vector(..., approximate=...)` opts into AuraDB's
  opt-in HNSW preview (shipped in v0.6.0; v0.7.0 added the typed `HnswOptions`
  dataclass with `m` / `ef_construction` / `ef_search` and a `fallback` of
  `"exact"` (default) / `"error"`, gated on `hnsw_preview`). Exact search remains
  the default and correctness baseline; not production ANN.
- [ ] Exact-vs-approximate comparison helper (v0.8.0).
- [ ] Batch vector-query helpers.
- [ ] Vector validation utilities.

## Backend adapters

Adapters make the same typed API useful with existing databases; they support a
subset of AuraDB features and report the rest honestly.

- [x] Honest capability errors — a backend that cannot serve a requested feature
  raises a structured `AuraCapabilityError` rather than silently emulating it; this
  is the shipped behavior and a standing invariant, not a future item.
- [ ] Clearer capability matrix per backend.
- [~] More SQL-backend parity where feasible.
- [ ] Better unsupported-feature error messages and guidance (v0.8.0).
- [ ] Optional backend integration test matrix.

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

- [x] Per-query `timeout_ms` forwarding to the AuraDB native backend — shipped in
  v0.6.1 so `QueryBuilder.timeout(ms)` is enforced cooperatively over the wire
  (an over-budget read returns `AuraTimeoutError`).
- [x] Live AuraDB conformance coverage — shipped from v0.6.1: connector-driven
  conformance for facets/aggregations, ranked pagination, and query timeouts (plus
  cluster variants) exercise this connector against a running server. More scenarios
  remain open below.
- [x] Executable docs examples — shipped in v0.7.0 (`examples/auradb_group_by.py`,
  `auradb_query_profile.py`, `auradb_cursor_resume.py`, `auradb_ann_preview.py`,
  smoke-tested against the in-memory reference engine).
- [ ] More live AuraDB conformance scenarios (including v1.4 workflows).
- [ ] Search / ranking regression fixtures.
- [ ] Backend-matrix CI improvements.
- [ ] Packaging-metadata checks.
- [ ] More executable production single-node auth/TLS/search/vector examples (v0.8.0).

## Later / not v0.8.0 by default

These are real future directions, but they are explicitly **out of v0.8.0 scope**
unless intentionally re-scoped. They do not weaken the v0.8.0 production-connection
and search-quality focus.

- Production-HA automation.
- Unbounded retry / failover abstraction.
- All-backend parity for AuraDB-specific analytics.
- Full ORM.
- Production ANN claims.

## Not currently planned for immediate work

These are listed so the boundary is explicit. They are not promised and not
implied by any other section.

- Making all backends support every AuraDB feature.
- Unbounded automatic cluster retries.
- Hiding AuraDB capability errors.
- Replacing AuraDB server-side semantics inside the connector.
