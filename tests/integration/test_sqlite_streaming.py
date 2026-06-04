"""Integration tests for SQLite paged streaming."""

from __future__ import annotations

import pytest

from aura import Aura, AuraModel, Field
from aura.client import Client


class Row(AuraModel):
    id: int = Field(primary_key=True)
    n: int


@pytest.fixture
async def db():
    async with Aura.connect("sqlite://", models=[Row]) as client:
        await client.bulk_insert(Row, [Row(id=i, n=i * 2) for i in range(25)])
        yield client


async def test_stream_returns_all_rows_in_order(db: Client) -> None:
    seen = [r.id async for r in db.query(Row).order_by(Row.id.asc()).stream(batch_size=10)]
    assert seen == list(range(25))


async def test_stream_respects_batch_size(db: Client) -> None:
    seen = 0
    async for _ in db.query(Row).order_by(Row.id.asc()).stream(batch_size=7):
        seen += 1
    assert seen == 25


async def test_stream_with_filter(db: Client) -> None:
    seen = [
        r.id
        async for r in db.query(Row).where(Row.id >= 20).order_by(Row.id.asc()).stream(batch_size=2)
    ]
    assert seen == [20, 21, 22, 23, 24]


async def test_early_break_records_cancellation(db: Client) -> None:
    got = []
    agen = db.query(Row).order_by(Row.id.asc()).stream(batch_size=5)
    async for r in agen:
        got.append(r)
        if len(got) == 6:
            break
    await agen.aclose()
    assert len(got) == 6
    assert db.metrics.stream_cancellations == 1


async def test_invalid_batch_size_raises(db: Client) -> None:
    from aura.errors import AuraQueryError

    with pytest.raises(AuraQueryError):
        db.query(Row).stream(batch_size=0)
