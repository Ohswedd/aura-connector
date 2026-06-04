"""MongoDB backend: document-native storage through the typed API.

Set ``AURA_TEST_MONGODB_DSN`` (or ``MONGODB_DSN``), for example
``mongodb://localhost:27017/aura_test``. With no DSN the example prints instructions and
exits cleanly. Lists and documents are stored natively; vector search and transactions are
reported through capabilities and only used where the deployment supports them.

Run: ``python examples/mongodb_backend.py`` (requires ``pip install aura-connector[mongodb]``)
Start a local server: ``docker compose -f docker-compose.backends.yml up -d mongodb``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, Field, Model


class Article(Model):
    id: int = Field(primary_key=True)
    title: str = Field(index=True)
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)


async def main() -> None:
    dsn = os.environ.get("AURA_TEST_MONGODB_DSN") or os.environ.get("MONGODB_DSN")
    if not dsn:
        print(
            "No MongoDB DSN found. Set AURA_TEST_MONGODB_DSN, e.g.\n"
            "  export AURA_TEST_MONGODB_DSN=mongodb://localhost:27017/aura_test\n"
            "Then: pip install aura-connector[mongodb] and re-run."
        )
        return

    async with Aura.connect(dsn, models=[Article]) as db:
        print("connected:", db.backend.name, "| transactions:", db.capabilities().transactions)
        await db.upsert(
            Article,
            key={Article.id: 1},
            values={
                Article.title: "Welcome",
                Article.tags: ["intro"],
                Article.meta: {"lang": "en"},
            },
        )
        found = await db.query(Article).where(Article.title.startswith("Wel")).all()
        print("found:", [(a.title, a.tags, a.meta) for a in found])


if __name__ == "__main__":
    asyncio.run(main())
