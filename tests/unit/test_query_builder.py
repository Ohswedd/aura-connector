"""Tests for the fluent query builder."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from aura import AuraModel, Field, Vector
from aura.errors import AuraNotFoundError, AuraQueryError
from aura.query.ast import QueryNode
from aura.query.builder import QueryBuilder, QueryResult, TraverseBuilder


class Widget(AuraModel):
    id: int = Field(primary_key=True)
    name: str
    embedding: Vector[2] | None = None


class RecordingExecutor:
    def __init__(self, rows: list[Any] | None = None, count: int = 0) -> None:
        self.rows = rows or []
        self.count = count
        self.nodes: list[QueryNode] = []

    async def run(self, node: QueryNode, model: type[AuraModel]) -> QueryResult:
        self.nodes.append(node)
        return QueryResult(rows=list(self.rows), count=self.count, affected=len(self.rows))

    async def stream(
        self, node: QueryNode, model: type[AuraModel], batch_size: int
    ) -> AsyncIterator[Any]:
        self.nodes.append(node)
        for row in self.rows:
            yield row


def builder(executor: RecordingExecutor) -> QueryBuilder:
    return QueryBuilder(Widget, executor)


def test_builder_is_immutable() -> None:
    ex = RecordingExecutor()
    base = builder(ex)
    extended = base.where(Widget.id == 1)
    assert base.query.filters == ()
    assert len(extended.query.filters) == 1


async def test_all_returns_rows() -> None:
    ex = RecordingExecutor(rows=[Widget(id=1, name="a")])
    rows = await builder(ex).all()
    assert len(rows) == 1


async def test_one_requires_single() -> None:
    ex = RecordingExecutor(rows=[Widget(id=1, name="a"), Widget(id=2, name="b")])
    with pytest.raises(AuraQueryError):
        await builder(ex).one()


async def test_one_not_found() -> None:
    ex = RecordingExecutor(rows=[])
    with pytest.raises(AuraNotFoundError):
        await builder(ex).one()


async def test_first_returns_none_when_empty() -> None:
    ex = RecordingExecutor(rows=[])
    assert await builder(ex).first() is None


async def test_await_builder_calls_one() -> None:
    ex = RecordingExecutor(rows=[Widget(id=1, name="a")])
    result = await builder(ex).find(id=1)
    assert result.id == 1


async def test_count_and_exists() -> None:
    ex = RecordingExecutor(count=3)
    assert await builder(ex).count() == 3
    assert await builder(ex).exists() is True


def test_find_builds_equality_filters() -> None:
    ex = RecordingExecutor()
    b = builder(ex).find(id=5, name="x")
    assert len(b.query.filters) == 2


def test_nearest_sets_vector_search() -> None:
    ex = RecordingExecutor()
    b = builder(ex).nearest(Widget.embedding, [1, 0], metric="cosine", limit=5)
    ir = b.explain()
    assert ir["vector"]["field"] == "embedding"
    assert ir["limit"] == 5


def test_invalid_metric() -> None:
    with pytest.raises(AuraQueryError):
        builder(RecordingExecutor()).nearest(Widget.embedding, [1, 0], metric="bogus")


def test_text_and_fusion() -> None:
    ex = RecordingExecutor()
    b = builder(ex).text(Widget.name, query="hello").fusion(alpha=0.6)
    ir = b.explain()
    assert ir["text"]["query"] == "hello"
    assert ir["fusion"]["alpha"] == 0.6


def test_invalid_fusion_alpha() -> None:
    with pytest.raises(AuraQueryError):
        builder(RecordingExecutor()).fusion(alpha=2.0)


def test_negative_limit_offset() -> None:
    with pytest.raises(AuraQueryError):
        builder(RecordingExecutor()).limit(-1)
    with pytest.raises(AuraQueryError):
        builder(RecordingExecutor()).offset(-1)


async def test_stream_yields_rows() -> None:
    ex = RecordingExecutor(rows=[Widget(id=1, name="a"), Widget(id=2, name="b")])
    seen = [w async for w in builder(ex).stream(batch_size=1)]
    assert len(seen) == 2


def test_explain_returns_ir() -> None:
    ir = builder(RecordingExecutor()).where(Widget.id == 1).explain()
    assert ir["operation"] == "select"


async def test_traverse_builder() -> None:
    ex = RecordingExecutor(rows=[Widget(id=1, name="a")])
    tb = TraverseBuilder(Widget, ex).start(Widget.id == 1).out("friends").max_depth(3).limit(10)
    ir = tb.explain()
    assert ir["operation"] == "traverse"
    assert ir["steps"] == ["friends"]
    assert ir["max_depth"] == 3
    rows = await tb.all()
    assert len(rows) == 1
