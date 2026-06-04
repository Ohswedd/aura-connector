"""Relationships and explicit loading with ``.include()``.

Aura never performs hidden lazy network IO: accessing a relationship that was not
included raises ``RelationshipNotLoadedError``.

Run: ``python examples/relationships.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model
from aura.errors import RelationshipNotLoadedError


class Author(Model):
    id: int = Field(primary_key=True)
    name: str


class Book(Model):
    id: int = Field(primary_key=True)
    author: Author = Field(link=True)
    title: str


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/library", models=[Author, Book]) as client:
        rowling = await client.insert(Author(id=1, name="J. K. Rowling"))
        await client.bulk_insert(
            Book,
            [
                Book(id=1, author=rowling, title="Philosopher's Stone"),
                Book(id=2, author=rowling, title="Chamber of Secrets"),
            ],
        )

        # Without include, the relationship is not loaded.
        book = await client.Book.find(id=1)
        try:
            _ = book.author
        except RelationshipNotLoadedError:
            print("author not loaded until included — as designed")

        # With include, the relationship is part of the typed result.
        book = await client.query(Book).where(Book.id == 1).include("author").one()
        print("included author:", book.author.name)

        # Reverse query: all books for an author.
        books = await client.query(Book).where(Book.author == rowling).all()
        print("books by author:", [b.title for b in books])


if __name__ == "__main__":
    asyncio.run(main())
