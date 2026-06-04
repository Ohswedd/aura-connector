"""Integration tests for client transactions, bulk ops, and graph traversal."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field
from aura.client import Client
from aura.errors import AuraConstraintError


class Person(AuraModel):
    id: int = Field(primary_key=True)
    name: str


class Friendship(AuraModel):
    id: int = Field(primary_key=True)
    person: Person = Field(link=True)
    friend: Person = Field(link=True)


async def make_client() -> Client:
    return await Client.connect("aura+memory://localhost/test", models=[Person, Friendship])


async def test_transaction_commit() -> None:
    client = await make_client()
    try:
        async with client.transaction() as tx:
            await tx.insert(Person(id=1, name="A"))
            await tx.insert(Person(id=2, name="B"))
        assert await client.query(Person).count() == 2
    finally:
        await client.close()


async def test_transaction_rollback_on_error() -> None:
    client = await make_client()
    try:
        with pytest.raises(RuntimeError):
            async with client.transaction() as tx:
                await tx.insert(Person(id=1, name="A"))
                raise RuntimeError("boom")
        assert await client.query(Person).count() == 0
    finally:
        await client.close()


async def test_explicit_rollback() -> None:
    client = await make_client()
    try:
        tx = client.transaction()
        await tx.begin()
        await tx.insert(Person(id=1, name="A"))
        await tx.rollback()
        assert await client.query(Person).count() == 0
    finally:
        await client.close()


async def test_bulk_insert_batches() -> None:
    client = await make_client()
    try:
        rows = [Person(id=i, name=f"p{i}") for i in range(25)]
        affected = await client.bulk_insert(Person, rows, batch_size=10)
        assert affected == 25
        assert await client.query(Person).count() == 25
    finally:
        await client.close()


async def test_duplicate_insert_raises_constraint() -> None:
    client = await make_client()
    try:
        await client.insert(Person(id=1, name="A"))
        with pytest.raises(AuraConstraintError):
            await client.insert(Person(id=1, name="B"))
    finally:
        await client.close()


async def test_insert_on_conflict_ignore() -> None:
    client = await make_client()
    try:
        await client.insert(Person(id=1, name="A"))
        await client.bulk_insert(Person, [Person(id=1, name="dup")], on_conflict="ignore")
        assert (await client.Person.find(id=1)).name == "A"
    finally:
        await client.close()


async def test_graph_traversal() -> None:
    client = await make_client()
    try:
        await client.bulk_insert(
            Person, [Person(id=1, name="A"), Person(id=2, name="B"), Person(id=3, name="C")]
        )
        await client.bulk_insert(
            Friendship,
            [
                Friendship(
                    id=1,
                    person=await client.Person.find(id=1),
                    friend=await client.Person.find(id=2),
                ),
            ],
        )
        # Traverse from person 1 out along the "friend" link of their friendships.
        results = await client.traverse(Friendship).start(Friendship.id == 1).out("friend").all()
        assert [p.id for p in results] == [2]
    finally:
        await client.close()


async def test_client_records_server_errors_in_metrics() -> None:
    client = await make_client()
    try:
        await client.insert(Person(id=1, name="A"))
        with pytest.raises(AuraConstraintError):
            await client.insert(Person(id=1, name="dup"))
        snap = client.metrics.snapshot()
        assert snap["error_count"].get("constraint_violation") == 1
        assert snap["errors_total"] == 1
    finally:
        await client.close()


async def test_client_with_telemetry_enabled_does_not_crash() -> None:
    # telemetry=True must work even when opentelemetry is not installed (soft bridge).
    client = await Client.connect("aura+memory://localhost/test", models=[Person], telemetry=True)
    try:
        await client.insert(Person(id=1, name="A"))
        rows = await client.query(Person).all()
        assert len(rows) == 1
        # Library-level metrics are always collected regardless of OTel availability.
        assert client.metrics.snapshot()["query_count"].get("insert") == 1
    finally:
        await client.close()
