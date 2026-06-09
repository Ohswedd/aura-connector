"""Public ranked-search cursor resume against AuraDB v1.3.0 (connector v0.7.0).

`builder.page(page_size=...)` fetches one ranked-search `Page` and returns its
opaque `next_cursor`. Unlike `search_pages` (which drives the whole loop in
process), the token can be persisted and handed back to `client.resume_search`
later — even from another process — to continue from exactly where you left off.

The token is opaque (never parse it) and its lifetime is server-bounded, so
resume promptly. For BM25/hybrid results that must stay stable under concurrent
writes, run the original search and the resume inside one snapshot transaction.

Run: ``python examples/auradb_cursor_resume.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/cursor_resume", models=[Doc]) as client:
        await client.bulk_insert(
            Doc,
            [Doc(id=i, body=("alpha " * (1 + i % 3)) + f"beta gamma {i}") for i in range(7)],
        )

        search = client.search(Doc).search_text("body", "alpha")

        # Fetch the first page and capture its opaque resume token.
        first = await search.page(page_size=2)
        print(f"page 1: ids={[r.id for r in first.items]} total={first.total}")
        token = first.next_cursor

        # ... the token could be persisted here and reloaded in another process ...

        page_number = 1
        while token is not None:
            page_number += 1
            page = await client.resume_search(search, token, page_size=2)
            print(f"page {page_number}: ids={[r.id for r in page.items]} has_more={page.has_more}")
            token = page.next_cursor


if __name__ == "__main__":
    asyncio.run(main())
