"""v0.7.0 public cursor resume (``Client.resume_search`` / ``QueryBuilder.page``).

Covers the public ``Page``/``SearchPage`` shape, the IR a single page fetch builds,
opaque-token handling, and an end-to-end resume from an externally-held token
against the in-memory reference engine (which implements the same ``search_page``
keyset contract as AuraDB v1.3.0).
"""

from __future__ import annotations

from typing import Any

import pytest

from aura import AuraModel, Field, Page, SearchPage, Vector
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.query.builder import QueryBuilder


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
    for i in range(6):
        body = ("alpha " * (1 + i % 3)) + f"beta gamma {i}"
        await client.insert(Doc(id=i, body=body, embedding=[float(i), 1.0]))
    return client


def test_page_shape_defaults() -> None:
    page: SearchPage = Page(items=[1, 2], next_cursor="tok", total=10)
    assert page.items == [1, 2]
    assert page.next_cursor == "tok"
    assert page.total == 10
    assert page.has_more is True
    assert Page(items=[]).has_more is False
    assert Page(items=[]).total is None


def test_page_requires_ranked_clause() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Doc).page(page_size=2)


def test_page_rejects_bad_args() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Doc).search_text("body", "alpha").page(page_size=0)
    with pytest.raises(AuraQueryError):
        _builder(Doc).search_text("body", "alpha").page(cursor="")


async def test_page_builds_search_page_ir_with_cursor() -> None:
    captured: dict[str, Any] = {}

    class _CaptureExecutor:
        async def resume_page(self, node, model, page_size, cursor):  # type: ignore[no-untyped-def]
            captured["ir"] = node.to_ir()
            captured["page_size"] = page_size
            captured["cursor"] = cursor
            return Page(items=[], next_cursor=None, total=None)

    builder = QueryBuilder(Doc, _CaptureExecutor())  # type: ignore[arg-type]
    await builder.search_text("body", "alpha").page(page_size=3, cursor="opaque-tok")
    assert "text_search" in captured["ir"]
    assert captured["page_size"] == 3
    assert captured["cursor"] == "opaque-tok"


async def test_resume_search_round_trips_external_token() -> None:
    client = await _seeded_client()
    try:
        reference = [r.id for r in await client.search(Doc).search_text("body", "alpha").all()]
        assert len(reference) == 6

        search = client.search(Doc).search_text("body", "alpha")
        first = await search.page(page_size=2)
        assert isinstance(first, Page)
        assert len(first.items) == 2
        assert first.has_more is True
        assert first.total == 6
        token = first.next_cursor
        assert isinstance(token, str) and token

        # Resume from the externally-held opaque token (as if persisted/reloaded).
        collected = [r.id for r in first.items]
        cursor: str | None = token
        while cursor is not None:
            page = await client.resume_search(search, cursor, page_size=2)
            collected.extend(r.id for r in page.items)
            cursor = page.next_cursor
        assert collected == reference
        assert len(set(collected)) == len(collected)
    finally:
        await client.close()


async def test_resume_search_works_for_vector_clause() -> None:
    client = await _seeded_client()
    try:
        search = client.search(Doc).search_vector("embedding", [1.0, 1.0], top_k=6)
        first = await search.page(page_size=3)
        assert first.has_more is True
        page2 = await client.resume_search(search, first.next_cursor, page_size=3)
        ids = [r.id for r in first.items] + [r.id for r in page2.items]
        assert len(set(ids)) == len(ids)
    finally:
        await client.close()


async def test_resume_search_rejects_empty_token() -> None:
    client = await _seeded_client()
    try:
        search = client.search(Doc).search_text("body", "alpha")
        with pytest.raises(AuraQueryError):
            await client.resume_search(search, "", page_size=2)
    finally:
        await client.close()
