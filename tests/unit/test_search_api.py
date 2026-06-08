"""v0.5.0 search and ranking APIs: query-IR building, weight validation, ranked
execution against the reference engine, score exposure, and capability errors."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector, search_scores
from aura.backends.capabilities import BackendCapabilities
from aura.client import Client
from aura.config import parse_dsn
from aura.errors import AuraCapabilityError, AuraQueryError


class Article(AuraModel):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


def _builder(model: type[AuraModel]):
    # A standalone builder for IR-shape assertions (no executor calls).
    from aura.query.builder import QueryBuilder

    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


# --- IR building -------------------------------------------------------------


def test_text_search_api_builds_query_ir() -> None:
    ir = _builder(Article).search_text("body", "vector search").query.to_ir()
    ts = ir["text_search"]
    assert ts["field"] == "body"
    assert ts["query"] == "vector search"
    assert ts["rank"] == "bm25"
    assert ts["operator"] == "or"


def test_text_search_bm25_options() -> None:
    ir = (
        _builder(Article)
        .search_text("body", "x", rank="bm25", operator="and", k1=1.5, b=0.4, limit=5)
        .query.to_ir()
    )
    ts = ir["text_search"]
    assert ts["operator"] == "and"
    assert ts["k1"] == 1.5
    assert ts["b"] == 0.4
    assert ir["limit"] == 5
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "x", rank="nonsense")


def test_vector_search_api_builds_query_ir() -> None:
    ir = _builder(Article).search_vector("embedding", [1.0, 0.0, 0.0], top_k=7).query.to_ir()
    assert ir["vector"]["field"] == "embedding"
    assert ir["vector"]["query"] == [1.0, 0.0, 0.0]
    assert ir["limit"] == 7


def test_hybrid_search_api_builds_query_ir() -> None:
    ir = (
        _builder(Article)
        .search_hybrid(
            "body",
            "alpha",
            "embedding",
            [1.0, 0.0, 0.0],
            weights=(0.7, 0.3),
            fusion="reciprocal_rank_fusion",
            top_k=4,
        )
        .query.to_ir()
    )
    h = ir["hybrid"]
    assert h["text_field"] == "body"
    assert h["vector_field"] == "embedding"
    assert h["weights"] == {"text": 0.7, "vector": 0.3}
    assert h["fusion"] == "reciprocal_rank_fusion"
    assert h["top_k"] == 4
    assert ir["limit"] == 4


def test_hybrid_search_weight_validation() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Article).search_hybrid(
            "body", "x", "embedding", [1.0, 0.0, 0.0], weights=(0.0, 0.0)
        )
    with pytest.raises(AuraQueryError):
        _builder(Article).search_hybrid(
            "body", "x", "embedding", [1.0, 0.0, 0.0], weights=(-1.0, 0.5)
        )
    with pytest.raises(AuraQueryError):
        _builder(Article).search_hybrid("body", "x", "embedding", [1.0], fusion="bogus")


# --- execution against the reference engine ----------------------------------


def _doc(model, id_: int, body: str, vec: list[float]):
    return model(id=id_, body=body, embedding=vec)


async def _seeded_client():
    config = parse_dsn("aura+memory://localhost/test")
    from aura.transport.memory import MemoryTransport, ReferenceServer

    client = Client(config, MemoryTransport(ReferenceServer()), [Article])
    await client._open()
    await client.insert(_doc(Article, 1, "raft consensus raft", [1.0, 0.0, 0.0]))
    await client.insert(_doc(Article, 2, "the raft module across many replicas", [0.0, 1.0, 0.0]))
    await client.insert(_doc(Article, 3, "storage compaction and flushing", [0.0, 0.0, 1.0]))
    return client


async def test_text_search_ranks_and_scores() -> None:
    client = await _seeded_client()
    try:
        rows = await client.search(Article).search_text("body", "raft").all()
        ids = [r.id for r in rows]
        assert ids[0] == 1  # the dense, short doc ranks first
        assert set(ids) == {1, 2}
        scores = search_scores(rows[0])
        assert scores.score is not None
        assert scores.rank == 1
    finally:
        await client.close()


async def test_hybrid_search_result_scores() -> None:
    client = await _seeded_client()
    try:
        rows = (
            await client.search(Article)
            .search_hybrid("body", "raft", "embedding", [0.0, 1.0, 0.0], top_k=3)
            .all()
        )
        assert rows
        # The fused, text+vector winner exposes both component scores.
        top = search_scores(rows[0])
        assert top.score is not None
        assert top.rank == 1
        # At least one row carries both component scores.
        assert any(
            search_scores(r).text_score is not None and search_scores(r).vector_score is not None
            for r in rows
        )
    finally:
        await client.close()


async def test_explain_analyze_search_ir() -> None:
    client = await _seeded_client()
    try:
        builder = client.search(Article).search_text("body", "raft")
        # Client-side IR inspection (explain returns the Query IR).
        ir = builder.explain()
        assert "text_search" in ir
    finally:
        await client.close()


# --- capability negotiation --------------------------------------------------


def test_capability_negotiation_search_features() -> None:
    config = parse_dsn("aura+memory://localhost/test")
    from aura.transport.memory import MemoryTransport, ReferenceServer

    client = Client(config, MemoryTransport(ReferenceServer()), [Article])
    caps = client.capabilities()
    assert caps.supports("full_text_search")
    assert caps.supports("hybrid_search")
    assert caps.supports("vector_search")


class _NoSearchBackend:
    name = "sql"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(name="sql")  # all search flags default False


def test_capability_error_on_sql_backend() -> None:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, backend=_NoSearchBackend())  # type: ignore[arg-type]
    with pytest.raises(AuraCapabilityError):
        client._check_search_capabilities({"text_search": {"field": "body", "query": "x"}})
    with pytest.raises(AuraCapabilityError):
        client._check_search_capabilities({"hybrid": {}})
    with pytest.raises(AuraCapabilityError):
        client._check_search_capabilities({"vector": {}})


def test_memory_backend_supports_search_features() -> None:
    # The reference engine implements BM25 and hybrid, so it advertises them and
    # does not raise a capability error for ranked search.
    config = parse_dsn("aura+memory://localhost/test")
    from aura.transport.memory import MemoryTransport, ReferenceServer

    client = Client(config, MemoryTransport(ReferenceServer()), [Article])
    client._check_search_capabilities({"text_search": {"field": "body", "query": "x"}})
    client._check_search_capabilities({"hybrid": {}})


class _NamedNoSearchBackend:
    def __init__(self, name: str) -> None:
        self._name = name

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(name=self._name)


@pytest.mark.parametrize("backend_name", ["sqlite", "postgres", "mysql", "mongodb", "redis"])
def test_search_unsupported_backend_message_names_backend(backend_name: str) -> None:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, backend=_NamedNoSearchBackend(backend_name))  # type: ignore[arg-type]
    with pytest.raises(AuraCapabilityError) as exc:
        client._check_search_capabilities({"text_search": {"field": "body", "query": "x"}})
    # The error names the backend and the missing capability for actionable branching.
    assert backend_name in str(exc.value)
    assert exc.value.context.get("backend") == backend_name
    assert exc.value.context.get("capability") == "full_text_search"


def test_search_invalid_empty_query() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "")
    with pytest.raises(AuraQueryError):
        _builder(Article).search_text("body", "   ")
    with pytest.raises(AuraQueryError):
        _builder(Article).search_hybrid("body", "  ", "embedding", [1.0, 0.0, 0.0])


def test_search_invalid_top_k() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Article).search_vector("embedding", [1.0, 0.0, 0.0], top_k=0)
    with pytest.raises(AuraQueryError):
        _builder(Article).search_hybrid("body", "x", "embedding", [1.0, 0.0, 0.0], top_k=-1)


def test_search_scores_missing_fields_safe() -> None:
    # An instance from a non-ranked query has no score attributes; the accessor
    # returns all-None rather than raising.
    art = Article(id=1, body="x", embedding=[0.0, 0.0, 0.0])
    s = search_scores(art)
    assert s.score is None
    assert s.text_score is None
    assert s.vector_score is None
    assert s.rank is None


def test_search_capabilities_from_v1_1_server_payload() -> None:
    # Simulate the native backend learning a v1.1.0 server's advertised
    # capabilities: BM25, hybrid, and exact vector are supported.
    from aura.backends.auradb_native import AuraDBNativeBackend
    from aura.observability import Metrics

    backend = AuraDBNativeBackend(parse_dsn("auradb://localhost/x"), Metrics(), _bridge())
    backend._server_caps = {
        "full_text_search",
        "full_text_bm25_ranking",
        "hybrid_search",
        "vector_exact_search",
    }
    caps = backend.capabilities()
    assert caps.full_text_search is True
    assert caps.hybrid_search is True
    assert caps.vector_search is True


def test_search_capabilities_from_older_server_fails_cleanly() -> None:
    # A pre-v1.1.0 server advertises basic full-text but NOT BM25 or hybrid, so the
    # connector reports them unsupported and a ranked-search query fails clearly.
    from aura.backends.auradb_native import AuraDBNativeBackend
    from aura.observability import Metrics

    backend = AuraDBNativeBackend(parse_dsn("auradb://localhost/x"), Metrics(), _bridge())
    backend._server_caps = {"full_text_search", "vector_exact_search"}  # no bm25/hybrid
    caps = backend.capabilities()
    assert caps.full_text_search is False  # BM25 search_text unsupported
    assert caps.hybrid_search is False
    assert caps.vector_search is True

    config = parse_dsn("auradb://localhost/x")
    client = Client(config, backend=backend)
    with pytest.raises(AuraCapabilityError):
        client._check_search_capabilities({"hybrid": {}})
    with pytest.raises(AuraCapabilityError):
        client._check_search_capabilities({"text_search": {"field": "body", "query": "x"}})


def _bridge():
    from aura.client import _TelemetryBridge
    from aura.observability import TelemetryConfig

    return _TelemetryBridge(TelemetryConfig.from_value(None))
