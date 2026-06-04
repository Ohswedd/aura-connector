"""Integration tests for SQLite transactions."""

from __future__ import annotations

import pytest

from aura import Aura, AuraModel, Field
from aura.client import Client


class Entry(AuraModel):
    id: int = Field(primary_key=True)
    label: str


@pytest.fixture
async def db():
    async with Aura.connect("sqlite://", models=[Entry]) as client:
        yield client


async def test_commit_persists_changes(db: Client) -> None:
    async with db.transaction() as tx:
        await tx.insert(Entry(id=1, label="a"))
        await tx.insert(Entry(id=2, label="b"))
    assert await db.query(Entry).count() == 2


async def test_rollback_on_error_discards_changes(db: Client) -> None:
    with pytest.raises(RuntimeError):
        async with db.transaction() as tx:
            await tx.insert(Entry(id=1, label="a"))
            raise RuntimeError("boom")
    assert await db.query(Entry).count() == 0


async def test_explicit_rollback(db: Client) -> None:
    tx = db.transaction()
    await tx.begin()
    await tx.insert(Entry(id=1, label="a"))
    await tx.rollback()
    assert await db.query(Entry).count() == 0


async def test_capabilities_declare_transactions(db: Client) -> None:
    assert db.capabilities().transactions is True
