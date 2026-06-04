"""Integration tests for query execution + hydration through the client."""

from __future__ import annotations

import pytest


async def seed(client, m):
    ws = await client.insert(m.Workspace(id=1, name="Acme"))
    await client.bulk_insert(
        m.User,
        [
            m.User(
                id=1, workspace=ws, name="Alice", email="alice@acme.com", embedding=[1, 0, 0, 0]
            ),
            m.User(id=2, workspace=ws, name="Bob", email="bob@acme.com", embedding=[0, 1, 0, 0]),
        ],
    )
    await client.bulk_insert(
        m.Order,
        [
            m.Order(id=1, user=await client.User.find(id=1), amount_cents=500),
            m.Order(id=2, user=await client.User.find(id=1), amount_cents=700),
        ],
    )
    return ws


async def test_insert_returns_hydrated(client, models) -> None:
    ws = await client.insert(models.Workspace(id=1, name="Acme"))
    assert isinstance(ws, models.Workspace)
    assert ws.name == "Acme"


async def test_filter_order_limit(client, models) -> None:
    await seed(client, models)
    User = models.User
    users = await (
        client.query(User)
        .where(User.email.contains("acme.com"))
        .order_by(User.id.desc())
        .limit(10)
        .all()
    )
    assert [u.id for u in users] == [2, 1]


async def test_projection_partial(client, models) -> None:
    await seed(client, models)
    User = models.User
    rows = await client.query(User).select(User.id, User.name).all()
    assert rows[0].name is not None
    with pytest.raises(AttributeError):
        _ = rows[0].email  # not selected


async def test_include_link(client, models) -> None:
    await seed(client, models)
    User = models.User
    user = await client.query(User).where(User.id == 1).include("workspace").one()
    assert user.workspace.name == "Acme"


async def test_reverse_collection_query(client, models) -> None:
    await seed(client, models)
    Order = models.Order
    orders = await client.query(Order).where(Order.user == await client.User.find(id=1)).all()
    assert {o.id for o in orders} == {1, 2}


async def test_count_and_exists(client, models) -> None:
    await seed(client, models)
    User = models.User
    assert await client.query(User).count() == 2
    assert await client.query(User).where(User.name == "Bob").exists() is True
    assert await client.query(User).where(User.name == "Zed").exists() is False


async def test_vector_search_returns_scores(client, models) -> None:
    Document = models.Document
    ws = await client.insert(models.Workspace(id=1, name="Acme"))
    await client.bulk_insert(
        Document,
        [
            Document(id=1, workspace=ws, title="a", body="x", embedding=[1, 0, 0]),
            Document(id=2, workspace=ws, title="b", body="y", embedding=[0, 1, 0]),
            Document(id=3, workspace=ws, title="c", body="z", embedding=[0.9, 0.1, 0]),
        ],
    )
    matches = await (
        client.search(Document)
        .nearest(Document.embedding, [1, 0, 0], metric="cosine")
        .limit(2)
        .all()
    )
    assert matches[0].title == "a"
    assert matches[0].__score__ == pytest.approx(1.0)
    assert len(matches) == 2


async def test_hybrid_search(client, models) -> None:
    Document = models.Document
    ws = await client.insert(models.Workspace(id=1, name="Acme"))
    await client.bulk_insert(
        Document,
        [
            Document(
                id=1, workspace=ws, title="refund policy", body="how to refund", embedding=[1, 0, 0]
            ),
            Document(id=2, workspace=ws, title="billing", body="invoices", embedding=[0, 1, 0]),
        ],
    )
    results = await (
        client.search(Document)
        .text(Document.title, Document.body, query="refund")
        .similar_to(Document.embedding, [1, 0, 0])
        .fusion(alpha=0.5)
        .limit(5)
        .all()
    )
    assert results[0].title == "refund policy"


async def test_update_and_delete(client, models) -> None:
    await seed(client, models)
    User = models.User
    updated = await client.update(User).where(User.id == 2).set(name="Bobby").execute()
    assert updated == 1
    assert (await client.User.find(id=2)).name == "Bobby"
    deleted = await client.delete(User).where(User.id == 2).execute()
    assert deleted == 1
    assert await client.query(User).count() == 1


async def test_upsert_insert_then_update(client, models) -> None:
    Workspace = models.Workspace
    await client.insert(Workspace(id=1, name="Acme"))
    first = await client.upsert(Workspace, key={Workspace.id: 2}, values={Workspace.name: "New"})
    assert first.name == "New"
    second = await client.upsert(
        Workspace, key={Workspace.id: 2}, values={Workspace.name: "Renamed"}
    )
    assert second.name == "Renamed"
    assert await client.query(Workspace).count() == 2


async def test_streaming(client, models) -> None:
    await seed(client, models)
    seen = [u async for u in client.query(models.User).stream(batch_size=1)]
    assert len(seen) == 2


async def test_raw_with_bound_params(client, models) -> None:
    await seed(client, models)
    rows = await client.raw("SELECT * FROM User WHERE id = $uid", {"uid": 1})
    assert rows[0]["name"] == "Alice"


async def test_document_path_predicate(client, models) -> None:
    Document = models.Document
    ws = await client.insert(models.Workspace(id=1, name="Acme"))
    await client.bulk_insert(
        Document,
        [
            Document(
                id=1,
                workspace=ws,
                title="a",
                body="x",
                metadata={"status": "published"},
                embedding=[1, 0, 0],
            ),
            Document(
                id=2,
                workspace=ws,
                title="b",
                body="y",
                metadata={"status": "draft"},
                embedding=[0, 1, 0],
            ),
        ],
    )
    published = await client.query(Document).where(Document.metadata["status"] == "published").all()
    assert [d.id for d in published] == [1]
