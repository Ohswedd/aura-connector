"""Optional Redis integration tests (limited key-value backend).

Gated on ``AURA_TEST_REDIS_DSN`` (for example ``redis://localhost:6379/0``) and skipped
cleanly when unset. Start a server with
``docker compose -f docker-compose.backends.yml up -d redis``.
"""

from __future__ import annotations

import os

import pytest

from aura import Aura, AuraModel, Field
from aura.errors import AuraBackendCapabilityError, AuraConstraintError

DSN = os.environ.get("AURA_TEST_REDIS_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set AURA_TEST_REDIS_DSN to run")


class Session(AuraModel):
    id: str = Field(primary_key=True)
    user: str
    payload: dict[str, str] = Field(default_factory=dict)


@pytest.fixture
async def db():
    assert DSN is not None
    async with Aura.connect(DSN, models=[Session]) as client:
        await client.backend.drop_schema([Session])
        try:
            yield client
        finally:
            await client.backend.drop_schema([Session])


async def test_key_value_roundtrip(db) -> None:
    await db.insert(Session(id="s1", user="ada", payload={"theme": "dark"}))
    got = await db.Session.find(id="s1")
    assert got.user == "ada" and got.payload == {"theme": "dark"}


async def test_exists_and_count_by_prefix(db) -> None:
    await db.bulk_insert(Session, [Session(id="a", user="x"), Session(id="b", user="y")])
    assert await db.query(Session).where(Session.id == "a").exists() is True
    assert await db.query(Session).count() == 2


async def test_delete_by_primary_key(db) -> None:
    await db.insert(Session(id="s1", user="ada"))
    assert await db.delete(Session).where(Session.id == "s1").execute() == 1
    assert await db.query(Session).where(Session.id == "s1").exists() is False


async def test_duplicate_insert_raises_constraint(db) -> None:
    await db.insert(Session(id="s1", user="ada"))
    with pytest.raises(AuraConstraintError):
        await db.insert(Session(id="s1", user="bob"))


async def test_relational_filter_raises_capability_error(db) -> None:
    await db.insert(Session(id="s1", user="ada"))
    with pytest.raises(AuraBackendCapabilityError):
        await db.query(Session).where(Session.user == "ada").all()


async def test_capabilities_are_honest(db) -> None:
    caps = db.capabilities()
    assert caps.key_value is True
    assert caps.relationships is False
    assert caps.graph_traversal is False
    assert caps.vector_search is False
