"""Integration tests for SQLite schema creation, drop, and local diff."""

from __future__ import annotations

import pytest

from aura import Aura, AuraModel, Field
from aura.client import Client


class Note(AuraModel):
    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    body: str | None = None


class Author(AuraModel):
    id: int = Field(primary_key=True)
    name: str


class Article(AuraModel):
    id: int = Field(primary_key=True)
    author: Author = Field(link=True)
    headline: str


@pytest.fixture
async def db():
    async with Aura.connect("sqlite://", models=[Note]) as client:
        yield client


async def test_schema_is_created_on_connect(db: Client) -> None:
    # Inserting succeeds because the table was created at connect time.
    await db.insert(Note(id=1, title="hello"))
    assert await db.query(Note).count() == 1


async def test_create_schema_is_idempotent(db: Client) -> None:
    # Re-creating an existing schema must not error (CREATE TABLE IF NOT EXISTS).
    await db.backend.create_schema([Note])
    await db.insert(Note(id=1, title="hi"))
    assert await db.query(Note).count() == 1


async def test_drop_schema_removes_table(db: Client) -> None:
    await db.insert(Note(id=1, title="hi"))
    await db.backend.drop_schema([Note])
    await db.backend.create_schema([Note])
    assert await db.query(Note).count() == 0


async def test_link_relationship_creates_fk_column() -> None:
    async with Aura.connect("sqlite://", models=[Author, Article]) as db:
        author = await db.insert(Author(id=1, name="Ada"))
        await db.insert(Article(id=1, author=author, headline="Engines"))
        row = await db.Article.find(id=1)
        assert row.headline == "Engines"


async def test_diff_schema_reports_added_model(db: Client) -> None:
    plan = await db.backend.diff_schema([Note, Author])
    kinds = {c.kind for c in plan.changes}
    assert "add_model" in kinds
    assert any(c.target == "Author" for c in plan.changes)
