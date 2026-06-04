"""Optional MySQL/MariaDB integration tests.

Gated on ``AURA_TEST_MYSQL_DSN`` (for example
``mysql://root:password@localhost:3306/aura_test``) and skipped cleanly when unset. Start a
server with ``docker compose -f docker-compose.backends.yml up -d mysql``.
"""

from __future__ import annotations

import os

import pytest

from aura import Aura, AuraModel, Field
from aura.errors import AuraConstraintError

DSN = os.environ.get("AURA_TEST_MYSQL_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set AURA_TEST_MYSQL_DSN to run")


class MyItem(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)
    price: float = Field(default=0.0)
    tags: list[str] = Field(default_factory=list)


@pytest.fixture
async def db():
    assert DSN is not None
    async with Aura.connect(DSN, models=[MyItem]) as client:
        await client.backend.drop_schema([MyItem])
        await client.backend.create_schema([MyItem])
        try:
            yield client
        finally:
            await client.backend.drop_schema([MyItem])


async def test_crud_cycle(db) -> None:
    await db.insert(MyItem(id=1, name="alpha", price=1.5, tags=["x"]))
    await db.bulk_insert(MyItem, [MyItem(id=2, name="beta"), MyItem(id=3, name="gamma", price=9)])
    assert await db.query(MyItem).count() == 3
    got = await db.MyItem.find(id=1)
    assert got.name == "alpha" and got.tags == ["x"]
    assert await db.update(MyItem).where(MyItem.id == 2).set(price=5).execute() == 1
    assert await db.delete(MyItem).where(MyItem.id == 3).execute() == 1


async def test_upsert(db) -> None:
    await db.insert(MyItem(id=1, name="alpha", price=1.0))
    await db.upsert(MyItem, key={MyItem.id: 1}, values={MyItem.price: 50.0})
    assert (await db.MyItem.find(id=1)).price == 50.0


async def test_duplicate_raises_constraint(db) -> None:
    await db.insert(MyItem(id=1, name="alpha"))
    with pytest.raises(AuraConstraintError):
        await db.insert(MyItem(id=1, name="other"))


async def test_transaction_rollback(db) -> None:
    with pytest.raises(RuntimeError):
        async with db.transaction() as tx:
            await tx.insert(MyItem(id=7, name="temp"))
            raise RuntimeError("boom")
    assert await db.query(MyItem).where(MyItem.id == 7).exists() is False
