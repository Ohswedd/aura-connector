"""Optional PostgreSQL integration tests.

Gated on ``AURA_TEST_POSTGRES_DSN`` (for example
``postgresql://postgres:postgres@localhost:5432/aura_test``) and skipped cleanly when it is
unset, so the default test run needs no PostgreSQL server. Start one with
``docker compose -f docker-compose.backends.yml up -d postgres``.
"""

from __future__ import annotations

import os

import pytest

from aura import Aura, AuraModel, Field
from aura.errors import AuraBackendCapabilityError, AuraConstraintError

DSN = os.environ.get("AURA_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set AURA_TEST_POSTGRES_DSN to run")


class PgItem(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)
    price: float = Field(default=0.0)
    tags: list[str] = Field(default_factory=list)


@pytest.fixture
async def db():
    assert DSN is not None
    async with Aura.connect(DSN, models=[PgItem]) as client:
        await client.backend.drop_schema([PgItem])
        await client.backend.create_schema([PgItem])
        try:
            yield client
        finally:
            await client.backend.drop_schema([PgItem])


async def test_crud_cycle(db) -> None:
    await db.insert(PgItem(id=1, name="alpha", price=1.5, tags=["x"]))
    await db.bulk_insert(PgItem, [PgItem(id=2, name="beta"), PgItem(id=3, name="gamma", price=9)])
    got = await db.PgItem.find(id=1)
    assert got.name == "alpha" and got.tags == ["x"]
    assert await db.query(PgItem).count() == 3
    rows = await db.query(PgItem).where(PgItem.price >= 1).order_by(PgItem.price.desc()).all()
    assert [r.id for r in rows] == [3, 1]
    assert await db.update(PgItem).where(PgItem.id == 2).set(price=5).execute() == 1
    assert await db.delete(PgItem).where(PgItem.id == 3).execute() == 1
    assert await db.query(PgItem).count() == 2


async def test_upsert(db) -> None:
    await db.insert(PgItem(id=1, name="alpha", price=1.0))
    await db.upsert(PgItem, key={PgItem.id: 1}, values={PgItem.price: 50.0})
    assert (await db.PgItem.find(id=1)).price == 50.0


async def test_duplicate_raises_constraint(db) -> None:
    await db.insert(PgItem(id=1, name="alpha"))
    with pytest.raises(AuraConstraintError):
        await db.insert(PgItem(id=1, name="other"))


async def test_transaction_rollback(db) -> None:
    with pytest.raises(RuntimeError):
        async with db.transaction() as tx:
            await tx.insert(PgItem(id=7, name="temp"))
            raise RuntimeError("boom")
    assert await db.query(PgItem).where(PgItem.id == 7).exists() is False


async def test_streaming(db) -> None:
    await db.bulk_insert(PgItem, [PgItem(id=i, name=f"n{i}") for i in range(10)])
    seen = [r.id async for r in db.query(PgItem).order_by(PgItem.id.asc()).stream(batch_size=3)]
    assert seen == list(range(10))


async def test_vector_search_capability_error(db) -> None:
    with pytest.raises(AuraBackendCapabilityError):
        await db.search(PgItem).nearest(PgItem.name, [1, 0, 0]).all()
