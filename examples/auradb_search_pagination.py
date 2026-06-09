"""Stable ranked pagination against AuraDB v1.2.0 (connector v0.6.0).

`search_pages(page_size=...)` pages a ranked search (`search_text` /
`search_vector` / `search_hybrid`) by AuraDB's opaque, stable cursor tokens,
yielding `SearchResultPage` objects until the result is exhausted.

Run: ``python examples/auradb_search_pagination.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, search_scores


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/pagination", models=[Doc]) as client:
        await client.bulk_insert(
            Doc,
            [Doc(id=i, body=("alpha " * (1 + i % 3)) + f"beta gamma {i}") for i in range(7)],
        )

        page_number = 0
        async for page in client.search(Doc).search_text("body", "alpha").search_pages(page_size=2):
            page_number += 1
            ids = [row.id for row in page.rows]
            ranks = [search_scores(row).rank for row in page.rows]
            print(f"page {page_number}: ids={ids} ranks={ranks} has_more={page.has_more}")
            # `page.cursor` is the opaque token for the next page (server-issued).


if __name__ == "__main__":
    asyncio.run(main())
