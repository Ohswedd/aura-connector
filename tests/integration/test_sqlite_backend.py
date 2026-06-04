"""Integration tests for the SQLite backend (run by default; no external service)."""

from __future__ import annotations

import pytest

from aura import Aura, AuraModel, Field, Vector
from aura.client import Client
from aura.errors import AuraBackendCapabilityError, AuraConstraintError


class Product(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)
    price: float = Field(default=0.0)
    in_stock: bool = Field(default=True)
    tags: list[str] = Field(default_factory=list)
    spec: dict[str, str] = Field(default_factory=dict)
    embedding: Vector[3] | None = None


@pytest.fixture
async def db():
    async with Aura.connect("sqlite://", models=[Product]) as client:
        yield client


async def _seed(db: Client) -> None:
    await db.insert(
        Product(id=1, name="alpha", price=1.5, tags=["a"], spec={"k": "v"}, embedding=[1, 0, 0])
    )
    await db.bulk_insert(
        Product,
        [
            Product(id=2, name="beta", price=5.0, in_stock=False),
            Product(id=3, name="gamma", price=9.0),
        ],
    )


async def test_backend_identity_and_capabilities(db: Client) -> None:
    assert db.backend.name == "sqlite"
    caps = db.capabilities()
    assert caps.transactions and caps.json_fields and caps.raw_queries
    assert not caps.vector_search and not caps.graph_traversal


async def test_insert_and_find_roundtrip(db: Client) -> None:
    await _seed(db)
    got = await db.Product.find(id=1)
    assert got.name == "alpha"
    assert got.price == 1.5
    assert got.tags == ["a"]
    assert got.spec == {"k": "v"}
    assert list(got.embedding) == [1.0, 0.0, 0.0]


async def test_bool_field_roundtrips(db: Client) -> None:
    await _seed(db)
    assert (await db.Product.find(id=2)).in_stock is False
    assert (await db.Product.find(id=1)).in_stock is True


async def test_filter_order_limit_offset(db: Client) -> None:
    await _seed(db)
    rows = await db.query(Product).where(Product.price >= 1.0).order_by(Product.price.desc()).all()
    assert [r.id for r in rows] == [3, 2, 1]
    page = await db.query(Product).order_by(Product.id.asc()).limit(1).offset(1).all()
    assert [r.id for r in page] == [2]


async def test_string_predicates(db: Client) -> None:
    await _seed(db)
    assert [r.id for r in await db.query(Product).where(Product.name.contains("amm")).all()] == [3]
    assert [r.id for r in await db.query(Product).where(Product.name.startswith("be")).all()] == [2]
    assert [r.id for r in await db.query(Product).where(Product.name.endswith("ha")).all()] == [1]


async def test_in_and_boolean_combinators(db: Client) -> None:
    await _seed(db)
    rows = await db.query(Product).where(Product.id.in_([1, 3])).all()
    assert {r.id for r in rows} == {1, 3}


async def test_count_and_exists(db: Client) -> None:
    await _seed(db)
    assert await db.query(Product).count() == 3
    assert await db.query(Product).where(Product.price > 8).count() == 1
    assert await db.query(Product).where(Product.name == "beta").exists() is True
    assert await db.query(Product).where(Product.name == "zzz").exists() is False


async def test_update_and_delete(db: Client) -> None:
    await _seed(db)
    updated = await db.update(Product).where(Product.id == 2).set(price=12.0).execute()
    assert updated == 1
    assert (await db.Product.find(id=2)).price == 12.0
    deleted = await db.delete(Product).where(Product.id == 3).execute()
    assert deleted == 1
    assert await db.query(Product).count() == 2


async def test_upsert_inserts_then_updates(db: Client) -> None:
    await _seed(db)
    await db.upsert(Product, key={Product.id: 1}, values={Product.price: 100.0})
    assert (await db.Product.find(id=1)).price == 100.0
    await db.upsert(
        Product, key={Product.id: 4}, values={Product.name: "delta", Product.price: 4.0}
    )
    assert (await db.Product.find(id=4)).name == "delta"
    assert await db.query(Product).count() == 4


async def test_duplicate_primary_key_raises_constraint(db: Client) -> None:
    await _seed(db)
    with pytest.raises(AuraConstraintError):
        await db.insert(Product(id=1, name="dup"))


async def test_on_conflict_ignore(db: Client) -> None:
    await _seed(db)
    await db.bulk_insert(Product, [Product(id=1, name="ignored")], on_conflict="ignore")
    assert (await db.Product.find(id=1)).name == "alpha"


async def test_vector_search_raises_capability_error(db: Client) -> None:
    await _seed(db)
    with pytest.raises(AuraBackendCapabilityError) as info:
        await db.search(Product).nearest(Product.embedding, [1, 0, 0]).all()
    assert info.value.context["capability"] == "vector_search"


async def test_traversal_raises_capability_error(db: Client) -> None:
    await _seed(db)
    with pytest.raises(AuraBackendCapabilityError):
        await db.traverse(Product).start(Product.id == 1).all()


async def test_health_reports_sqlite_backend(db: Client) -> None:
    await _seed(db)
    health = await db.health()
    assert health["status"] == "ok"
    assert health["backend"] == "sqlite"
    assert health["capabilities"]["name"] == "sqlite"
