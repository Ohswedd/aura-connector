"""v0.6.0 ranked-pagination helper (`QueryBuilder.search_pages`).

End-to-end pagination against the in-memory reference engine (which implements the
same `search_page` keyset contract as AuraDB v1.2.0), plus the native backend's
`search_page` request-shape and the ranked-clause guard.
"""

from __future__ import annotations

from typing import Any

import pytest

from aura import AuraModel, Field, Vector
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge
from aura.query.builder import QueryBuilder, SearchResultPage


class Doc(AuraModel):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[2]


def _builder(model: type[AuraModel]) -> QueryBuilder:
    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


async def _seeded_client():
    from aura.client import Client
    from aura.transport.memory import MemoryTransport, ReferenceServer

    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()), [Doc])
    await client._open()
    # Six documents that all match "alpha", with a score spread.
    for i in range(6):
        body = ("alpha " * (1 + i % 3)) + f"beta gamma {i}"
        await client.insert(Doc(id=i, body=body, embedding=[float(i), 1.0]))
    return client


async def test_search_pages_reconstructs_ranked_order_no_duplicates() -> None:
    client = await _seeded_client()
    try:
        reference = [r.id for r in await client.search(Doc).search_text("body", "alpha").all()]
        assert len(reference) == 6

        collected: list[int] = []
        pages = 0
        last_page_had_cursor = True
        async for page in client.search(Doc).search_text("body", "alpha").search_pages(page_size=2):
            assert isinstance(page, SearchResultPage)
            assert len(page.rows) <= 2
            collected.extend(r.id for r in page.rows)
            pages += 1
            last_page_had_cursor = page.has_more
        assert collected == reference, "paging reconstructs the full ranked order"
        assert len(set(collected)) == len(collected), "no duplicate rows across pages"
        assert pages == 3, "6 rows / page_size 2 -> 3 pages"
        assert last_page_had_cursor is False, "the final page has no next cursor"
    finally:
        await client.close()


async def test_search_pages_vector_clause_paginates() -> None:
    client = await _seeded_client()
    try:
        reference = [
            r.id
            for r in await client.search(Doc).search_vector("embedding", [1.0, 1.0], top_k=6).all()
        ]
        collected: list[int] = []
        async for page in (
            client.search(Doc)
            .search_vector("embedding", [1.0, 1.0], top_k=6)
            .search_pages(page_size=4)
        ):
            collected.extend(r.id for r in page.rows)
        assert collected == reference
        assert len(set(collected)) == len(collected)
    finally:
        await client.close()


def test_search_pages_requires_ranked_clause() -> None:
    # A non-ranked query cannot be paged as a ranked cursor; the builder rejects
    # it synchronously before any I/O.
    with pytest.raises(AuraQueryError):
        _builder(Doc).search_pages(page_size=2)


def test_search_pages_rejects_non_positive_page_size() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Doc).search_text("body", "alpha").search_pages(page_size=0)


async def test_native_backend_builds_search_page_request() -> None:
    from aura.backends.auradb_native import AuraDBNativeBackend

    config = parse_dsn("auradb://localhost:7171/db")
    backend = AuraDBNativeBackend(
        config, Metrics(), _TelemetryBridge(TelemetryConfig.from_value(None))
    )

    captured: dict[str, Any] = {}

    async def fake_request(opcode: int, payload_obj: Any, txn_id: int = 0):
        captured["opcode"] = opcode
        captured["payload"] = payload_obj
        return 0, {"rows": [{"id": "p1", "__score__": 1.5}], "next_cursor": "abc123"}

    backend._request = fake_request  # type: ignore[assignment]

    select_ir = _builder(Doc).search_text("body", "alpha").query.to_ir()
    page_ir = {**select_ir, "operation": "search_page", "page_size": 3}
    result = await backend.execute_query(page_ir)

    payload = captured["payload"]
    assert payload["query"] == "search_page"
    assert payload["page_size"] == 3
    assert "cursor" not in payload, "no cursor key on the first page"
    # The wrapped `find` is the translated server FindQuery for the ranked search.
    assert payload["find"]["collection"] == "Doc"
    assert "text_search" in payload["find"]
    # The next-page token rides back in metadata.
    assert result.metadata["next_cursor"] == "abc123"
    assert len(result.rows) == 1

    # A cursor, when present, is forwarded.
    page_ir_cur = {**page_ir, "cursor": "tok"}
    await backend.execute_query(page_ir_cur)
    assert captured["payload"]["cursor"] == "tok"
