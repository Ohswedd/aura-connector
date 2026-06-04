"""Optional MongoDB integration tests.

Gated on ``AURA_TEST_MONGODB_DSN`` (for example ``mongodb://localhost:27017/aura_test``) and
skipped cleanly when unset. Start a server with
``docker compose -f docker-compose.backends.yml up -d mongodb``.
"""

from __future__ import annotations

import os

import pytest

from aura import Aura, AuraModel, Field
from aura.errors import AuraBackendCapabilityError

DSN = os.environ.get("AURA_TEST_MONGODB_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set AURA_TEST_MONGODB_DSN to run")


class Doc(AuraModel):
    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    score: float = Field(default=0.0)
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)


@pytest.fixture
async def db():
    assert DSN is not None
    async with Aura.connect(DSN, models=[Doc]) as client:
        await client.backend.drop_schema([Doc])
        await client.backend.create_schema([Doc])
        try:
            yield client
        finally:
            await client.backend.drop_schema([Doc])


async def test_document_crud(db) -> None:
    await db.insert(Doc(id=1, title="alpha", score=1.5, tags=["x"], meta={"k": "v"}))
    await db.bulk_insert(Doc, [Doc(id=2, title="beta"), Doc(id=3, title="gamma", score=9)])
    got = await db.Doc.find(id=1)
    assert got.tags == ["x"] and got.meta == {"k": "v"}
    assert await db.query(Doc).count() == 3
    rows = await db.query(Doc).where(Doc.score >= 1).order_by(Doc.score.desc()).all()
    assert [r.id for r in rows] == [3, 1]
    assert await db.update(Doc).where(Doc.id == 2).set(score=5).execute() == 1
    assert await db.delete(Doc).where(Doc.id == 3).execute() == 1


async def test_string_filters(db) -> None:
    await db.bulk_insert(Doc, [Doc(id=1, title="alpha"), Doc(id=2, title="beta")])
    rows = await db.query(Doc).where(Doc.title.startswith("al")).all()
    assert [r.id for r in rows] == [1]


async def test_upsert(db) -> None:
    await db.upsert(Doc, key={Doc.id: 1}, values={Doc.title: "x", Doc.score: 3.0})
    assert (await db.Doc.find(id=1)).score == 3.0


async def test_vector_search_capability_error(db) -> None:
    with pytest.raises(AuraBackendCapabilityError):
        await db.search(Doc).nearest(Doc.title, [1, 0, 0]).all()


async def test_transactions_reported_unsupported(db) -> None:
    assert db.capabilities().transactions is False
