"""v0.9.0 search-analyzer ergonomics: the analyzer option model, query-builder
helpers, client-side validation, live over-the-wire forwarding, capability
negotiation/gating, and report parsing.

These pin the honest v0.9.0 behaviors:

* ``AnalyzerOptions`` validates names client-side against the AuraDB presets;
* ``search_text(analyzer=…)`` / ``.analyzer(…)`` build the IR and validate names;
* the native backend forwards a non-default analyzer over the wire and omits a
  ``default`` one (byte-compatible with pre-v1.5 requests);
* a non-default analyzer requires the backend ``query_analyzers`` capability,
  negotiated from the server handshake, and otherwise raises
  ``AuraCapabilityError`` — never silently dropped;
* the report parsers read the new analyzer field and the comparison report;
* ``DEFAULT_ISOLATION`` stays ``snapshot``.
"""

from __future__ import annotations

import pytest

from aura import (
    ANALYZER_PRESETS,
    AnalyzerComparisonReport,
    AnalyzerOptions,
    AuraModel,
    Field,
    Vector,
)
from aura.backends.capabilities import CAPABILITY_FLAGS, BackendCapabilities
from aura.client import DEFAULT_ISOLATION, Client
from aura.config import parse_dsn
from aura.errors import AuraCapabilityError, AuraQueryError
from aura.search_quality import SearchEvalReport


class Article(AuraModel):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


def _builder(model: type[AuraModel]):
    from aura.query.builder import QueryBuilder

    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


# --- analyzer option model ---------------------------------------------------


def test_analyzer_options_model() -> None:
    assert AnalyzerOptions().name == "default"
    assert AnalyzerOptions().is_default
    for name in ANALYZER_PRESETS:
        opts = AnalyzerOptions(name)
        assert opts.name == name
        assert opts.to_ir() == name
    assert not AnalyzerOptions("simple").is_default
    with pytest.raises(AuraQueryError):
        AnalyzerOptions("stemming")


# --- query builder -----------------------------------------------------------


def test_query_builder_analyzer() -> None:
    # Keyword argument form.
    ir = _builder(Article).search_text("body", "backup restore", analyzer="simple").query.to_ir()
    assert ir["text_search"]["analyzer"] == "simple"
    # Chained form.
    ir2 = _builder(Article).search_text("body", "backup").analyzer("ascii_fold").query.to_ir()
    assert ir2["text_search"]["analyzer"] == "ascii_fold"
    # default analyzer is omitted from the IR (byte-compatible with pre-v1.5 queries).
    ir3 = _builder(Article).search_text("body", "backup", analyzer="default").query.to_ir()
    assert "analyzer" not in ir3["text_search"]
    ir4 = _builder(Article).search_text("body", "backup").query.to_ir()
    assert "analyzer" not in ir4["text_search"]


def test_unknown_analyzer_validation_if_client_side() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "x", analyzer="stemming")
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "x").analyzer("bogus")
    # .analyzer() without a search_text clause is a clear error, not a silent no-op.
    with pytest.raises(AuraQueryError):
        _builder(Article).analyzer("simple")


# --- capability gating -------------------------------------------------------


def test_analyzer_requires_capability() -> None:
    # The new flags exist and default to unsupported everywhere.
    assert "query_analyzers" in CAPABILITY_FLAGS
    assert "search_snippets" in CAPABILITY_FLAGS
    caps = BackendCapabilities(name="auradb", full_text_search=True)
    assert not caps.supports("query_analyzers")
    with pytest.raises(AuraCapabilityError):
        caps.require("query_analyzers")
    described = caps.describe()
    assert "query_analyzers" in described["unsupported"]


def test_analyzer_sent_to_auradb_native() -> None:
    # The native backend forwards a non-default analyzer over the wire on both the
    # ranked-text and hybrid clauses.
    from aura.backends.auradb_native import _translate_select

    ir = _builder(Article).search_text("body", "café", analyzer="ascii_fold").query.to_ir()
    server = _translate_select(ir)
    assert server["text_search"]["analyzer"] == "ascii_fold"


def test_analyzer_omitted_for_default() -> None:
    # A default (or absent) analyzer is never put on the wire — byte-compatible with
    # a pre-v1.5 request.
    from aura.backends.auradb_native import _translate_select

    ir = _builder(Article).search_text("body", "raft", analyzer="default").query.to_ir()
    assert "analyzer" not in _translate_select(ir).get("text_search", {})
    ir2 = _builder(Article).search_text("body", "raft").query.to_ir()
    assert "analyzer" not in _translate_select(ir2).get("text_search", {})


def test_analyzer_live_success_with_v15_capability() -> None:
    # When the server advertises ``query_analyzers``, the native backend's negotiated
    # capabilities allow a non-default analyzer (no degradation).
    from aura.backends.auradb_native import AuraDBNativeBackend
    from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge

    backend = AuraDBNativeBackend(
        parse_dsn("auradb://localhost:7171/x"),
        Metrics(),
        _TelemetryBridge(TelemetryConfig.from_value(None)),
    )
    # A v1.5 server handshake advertises the capability -> negotiated supported.
    backend._server_caps = {"full_text_bm25_ranking", "query_analyzers", "search_snippets"}
    caps = backend.capabilities()
    assert caps.supports("query_analyzers")
    assert caps.supports("search_snippets")
    caps.require("query_analyzers")  # does not raise
    # An older server that does not advertise it -> negotiated unsupported, so a
    # non-default analyzer would degrade with a capability error.
    backend._server_caps = {"full_text_bm25_ranking"}
    assert not backend.capabilities().supports("query_analyzers")
    assert not backend.capabilities().supports("search_snippets")


def test_english_basic_if_implemented() -> None:
    # english_basic is a shipped preset on both sides; it validates and rides the IR.
    assert "english_basic" in ANALYZER_PRESETS
    assert AnalyzerOptions("english_basic").name == "english_basic"
    ir = _builder(Article).search_text("body", "backups", analyzer="english_basic").query.to_ir()
    assert ir["text_search"]["analyzer"] == "english_basic"


# --- keyword analyzer, including hybrid (v0.9.0) -----------------------------


def test_keyword_analyzer_valid() -> None:
    # keyword is a first-class AnalyzerOptions value.
    assert "keyword" in ANALYZER_PRESETS
    opts = AnalyzerOptions("keyword")
    assert opts.name == "keyword"
    assert not opts.is_default
    assert opts.to_ir() == "keyword"


def test_keyword_analyzer_text_request() -> None:
    # keyword is forwarded over the wire for plain ranked-text search.
    from aura.backends.auradb_native import _translate_select

    ir = _builder(Article).search_text("body", "backup restore", analyzer="keyword").query.to_ir()
    assert ir["text_search"]["analyzer"] == "keyword"
    assert _translate_select(ir)["text_search"]["analyzer"] == "keyword"


def test_keyword_analyzer_hybrid_request_if_supported() -> None:
    # keyword is forwarded for hybrid search, both via the keyword argument and the
    # chained .analyzer() form, and reaches the wire.
    from aura.backends.auradb_native import _translate_select

    ir = (
        _builder(Article)
        .search_hybrid("body", "backup restore", "embedding", [1.0, 0.0, 0.0], analyzer="keyword")
        .query.to_ir()
    )
    assert ir["hybrid"]["analyzer"] == "keyword"
    assert _translate_select(ir)["hybrid"]["analyzer"] == "keyword"

    ir2 = (
        _builder(Article)
        .search_hybrid("body", "backup restore", "embedding", [1.0, 0.0, 0.0])
        .analyzer("keyword")
        .query.to_ir()
    )
    assert ir2["hybrid"]["analyzer"] == "keyword"

    # A default/absent hybrid analyzer is omitted from the IR and the wire — fully
    # byte-compatible with a pre-v1.5 hybrid request.
    ir3 = _builder(Article).search_hybrid("body", "x", "embedding", [1.0, 0.0, 0.0]).query.to_ir()
    assert "analyzer" not in ir3["hybrid"]
    assert "analyzer" not in _translate_select(ir3).get("hybrid", {})


def test_keyword_analyzer_requires_capability() -> None:
    # requested_analyzer detects keyword on a hybrid clause, so capability gating
    # applies to hybrid exactly as it does to plain text search.
    from aura.analyzers import requested_analyzer

    ir = (
        _builder(Article)
        .search_hybrid("body", "backup restore", "embedding", [1.0, 0.0, 0.0], analyzer="keyword")
        .query.to_ir()
    )
    assert requested_analyzer(ir) == "keyword"
    caps = BackendCapabilities(name="auradb", full_text_search=True, hybrid_search=True)
    assert not caps.supports("query_analyzers")
    with pytest.raises(AuraCapabilityError):
        caps.require("query_analyzers")


def test_keyword_analyzer_live_hybrid_success() -> None:
    # When a v1.5 server advertises query_analyzers, a keyword hybrid query is allowed
    # (no degradation) and the analyzer rides the translated request.
    from aura.backends.auradb_native import AuraDBNativeBackend, _translate_select
    from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge

    backend = AuraDBNativeBackend(
        parse_dsn("auradb://localhost:7171/x"),
        Metrics(),
        _TelemetryBridge(TelemetryConfig.from_value(None)),
    )
    backend._server_caps = {"full_text_bm25_ranking", "hybrid_search", "query_analyzers"}
    caps = backend.capabilities()
    assert caps.supports("query_analyzers")
    caps.require("query_analyzers")  # does not raise

    ir = (
        _builder(Article)
        .search_hybrid("body", "backup restore", "embedding", [1.0, 0.0, 0.0], analyzer="keyword")
        .query.to_ir()
    )
    assert _translate_select(ir)["hybrid"]["analyzer"] == "keyword"


def test_english_basic_lens_regression_report_or_builder() -> None:
    # english_basic is a shipped preset on both sides; the bare-`s` singular "lens"
    # rides the IR verbatim as a query term (AuraDB's conservative fold keeps
    # lens->lens, not lens->len). The connector makes no NLP claim of its own.
    ir = _builder(Article).search_text("body", "lens", analyzer="english_basic").query.to_ir()
    assert ir["text_search"]["analyzer"] == "english_basic"
    assert ir["text_search"]["query"] == "lens"
    # english_basic is also valid on a hybrid clause.
    ir2 = (
        _builder(Article)
        .search_hybrid("body", "lens", "embedding", [0.0, 0.0, 1.0], analyzer="english_basic")
        .query.to_ir()
    )
    assert ir2["hybrid"]["analyzer"] == "english_basic"


def test_unsupported_backend_analyzer() -> None:
    # A non-AuraDB backend likewise does not support analyzers (and is never
    # pretended to). require() names the backend and the missing capability.
    caps = BackendCapabilities(name="sqlite")
    with pytest.raises(AuraCapabilityError):
        caps.require("query_analyzers")


# --- end-to-end degradation against the reference engine ---------------------


def _doc(model: type[AuraModel], pk: int, body: str, vector: list[float]) -> AuraModel:
    return model(id=pk, body=body, embedding=vector)


async def _seeded_client() -> Client:
    config = parse_dsn("aura+memory://localhost/test")
    from aura.transport.memory import MemoryTransport, ReferenceServer

    client = Client(config, MemoryTransport(ReferenceServer()), [Article])
    await client._open()
    await client.insert(_doc(Article, 1, "raft consensus raft", [1.0, 0.0, 0.0]))
    await client.insert(_doc(Article, 2, "the raft module across replicas", [0.0, 1.0, 0.0]))
    return client


@pytest.mark.asyncio
async def test_analyzer_requires_capability_end_to_end() -> None:
    client = await _seeded_client()
    try:
        # Default analyzer (omitted) works unchanged.
        rows = await client.search(Article).search_text("body", "raft").all()
        assert rows
        # A non-default analyzer degrades with a capability error — the reference
        # engine does not advertise query_analyzers.
        with pytest.raises(AuraCapabilityError):
            await client.search(Article).search_text("body", "raft", analyzer="simple").all()
        with pytest.raises(AuraCapabilityError):
            await client.search(Article).search_text("body", "raft").analyzer("keyword").all()
    finally:
        await client.close()


# --- report parsing ----------------------------------------------------------


def test_search_eval_report_analyzer_parse() -> None:
    base = {
        "dataset": "analyzer",
        "mode": "bm25",
        "queries": 1,
        "documents": 2,
        "k": 10,
        "metrics": {"mrr_at_k": 1.0, "ndcg_at_k": 1.0, "recall_at_k": 1.0},
        "per_query": [],
        "warnings": [],
    }
    # New analyzer field is parsed.
    report = SearchEvalReport.from_dict({**base, "analyzer": "ascii_fold"})
    assert report.analyzer == "ascii_fold"
    # Pre-v1.5 reports omit it and default to "default".
    legacy = SearchEvalReport.from_dict(base)
    assert legacy.analyzer == "default"


def test_search_eval_report_analyzer_comparison_report() -> None:
    payload = {
        "dataset": "analyzer",
        "mode": "bm25",
        "k": 10,
        "analyzers": [
            {
                "analyzer": "simple",
                "metrics": {"mrr_at_k": 0.75, "ndcg_at_k": 0.74, "recall_at_k": 0.75},
                "warnings": [],
            },
            {
                "analyzer": "ascii_fold",
                "metrics": {"mrr_at_k": 0.91, "ndcg_at_k": 0.92, "recall_at_k": 1.0},
                "warnings": [],
            },
        ],
    }
    report = AnalyzerComparisonReport.from_dict(payload)
    assert report.dataset == "analyzer"
    assert [leg.analyzer for leg in report.analyzers] == ["simple", "ascii_fold"]
    assert report.metrics_for("ascii_fold").recall_at_k == 1.0
    assert report.metrics_for("missing") is None
    # from_json accepts a JSON string too.
    import json

    assert AnalyzerComparisonReport.from_json(json.dumps(payload)).k == 10


def test_default_isolation_still_snapshot() -> None:
    assert DEFAULT_ISOLATION == "snapshot"
    assert DEFAULT_ISOLATION != "serializable"
