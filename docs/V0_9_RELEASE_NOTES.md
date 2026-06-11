# Aura Connector v0.9.0 release notes

**Theme: live analyzer and snippet ergonomics for AuraDB search.**

Aura Connector v0.9.0 is the paired client for the AuraDB v1.5.0 line. It is an **additive,
backward-compatible** release that adds live analyzer and snippet search ergonomics for AuraDB.
There is **no wire-protocol change and no server protocol change required**: existing
`connect(...)` / `Client(...)` constructors are unchanged, and a v0.8.x connector also keeps
working unchanged against AuraDB v1.5.0 — it simply does not request analyzer or snippet fields.
The new AuraDB-only paths are **capability-gated** on the server's `query_analyzers` /
`search_snippets` capabilities.

See [COMPATIBILITY.md](COMPATIBILITY.md), [AURADB.md](AURADB.md),
[SEARCH_AND_RANKING.md](SEARCH_AND_RANKING.md), [TESTING.md](TESTING.md), and
[ROADMAP.md](ROADMAP.md).

## Shipped

Analyzers:

- **`AnalyzerOptions`** — typed analyzer selection for AuraDB search.
- **`search_text(..., analyzer=...)`** — per-call analyzer selection.
- **builder `.analyzer(...)`** — analyzer selection on the query builder.
- **Hybrid analyzer request support** — analyzer selection on hybrid text+vector search.
- **Capability-gated `query_analyzers` support** — the analyzer paths require the server to
  advertise `query_analyzers`; otherwise they raise a structured capability error.

Snippets:

- **Snippet request helpers** — request opt-in snippets/highlights on ranked text search.
- **Typed `SearchSnippet` / `SearchSnippetFragment` / `HighlightRange` models** — structured
  snippet results.
- **Snippet result parsing** — parses server snippet payloads into the typed models.
- **Byte-to-character range handling** — highlight ranges are converted from server byte
  offsets to character offsets for correct Python string slicing.

Search evaluation:

- **Analyzer-aware search-eval report parsing** — `aura.search_quality` parses analyzer-tagged
  reports.
- **Analyzer comparison report parsing** — parses `compare-analyzers` output.

Conformance and examples:

- **Live analyzer conformance support** against an AuraDB v1.5.0 server.
- **Live snippet conformance support** against an AuraDB v1.5.0 server.
- **Examples** for analyzer and snippet search (`examples/auradb_analyzer_search.py`,
  `examples/auradb_snippet_search.py`, `examples/auradb_search_eval_analyzers.py`).

## Unchanged

- **`DEFAULT_ISOLATION = snapshot`.**
- **`"serializable"` remains a deprecated alias** to snapshot only.
- **No unbounded retry/failover automation.**
- **No production HA abstraction.**
- **No production ANN claim.**
- **AuraDB-only search features require server capabilities.**

## Known limitations

- **`ConnectionProfile` is not a secret manager.**
- **Analyzer/snippet features require AuraDB v1.5+ capabilities** (`query_analyzers` /
  `search_snippets`).
- **Unsupported backends raise capability errors** for AuraDB-only search features rather than
  silently degrading.
- **`english_basic` is deterministic and small** — it is not full NLP.
- **Relevance scores are fixture-specific regression signals**, not universal benchmarks.
