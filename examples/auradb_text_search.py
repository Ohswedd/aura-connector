"""Ranked full-text (BM25) search against AuraDB v1.1.0.

Run against the in-process reference engine:
    python examples/auradb_text_search.py

Against a real server, swap the DSN for ``auradb://<host>:<port>/<db>``.
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, Vector, search_scores


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/search", models=[Doc]) as client:
        await client.bulk_insert(
            Doc,
            [
                Doc(id=1, body="raft consensus raft", embedding=[1.0, 0.0, 0.0]),
                Doc(id=2, body="the raft module coordinates replicas", embedding=[0.0, 1.0, 0.0]),
                Doc(id=3, body="storage compaction and flushing", embedding=[0.0, 0.0, 1.0]),
            ],
        )

        # BM25 ranked search: results are ordered by relevance, not just matched.
        rows = await client.search(Doc).search_text("body", "raft", rank="bm25").all()
        print("BM25 results:")
        for doc in rows:
            s = search_scores(doc)
            print(f"  #{s.rank} id={doc.id} score={s.score:.3f}")

        # AND semantics require every query term to be present.
        strict = await client.search(Doc).search_text("body", "raft module", operator="and").all()
        print(f"AND match count: {len(strict)}")


if __name__ == "__main__":
    asyncio.run(main())
