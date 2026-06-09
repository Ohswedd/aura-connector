"""Approximate-vector (HNSW) preview options against AuraDB v1.3.0 (connector v0.7.0).

Exact vector search is the default and the correctness baseline. `HnswOptions`
opts a `search_vector` call into the approximate (HNSW) preview, carrying the
index/search parameters (`m`, `ef_construction`, `ef_search`) and a `fallback`
policy: ``"exact"`` (default) runs exact search when a request falls below the
server's HNSW threshold, ``"error"`` returns a structured error instead.

Run: ``python examples/auradb_ann_preview.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, HnswOptions, Model, Vector, search_scores


class Item(Model):
    id: int = Field(primary_key=True)
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/ann_preview", models=[Item]) as client:
        await client.bulk_insert(
            Item,
            [
                Item(id=0, embedding=[1.0, 0.0, 0.0]),
                Item(id=1, embedding=[0.9, 0.1, 0.0]),
                Item(id=2, embedding=[0.0, 1.0, 0.0]),
                Item(id=3, embedding=[0.0, 0.0, 1.0]),
            ],
        )

        # Approximate search with tuned HNSW parameters; fall back to exact search
        # below the server's threshold so results stay correct.
        options = HnswOptions(m=16, ef_construction=200, ef_search=64, fallback="exact")
        rows = (
            await client.search(Item)
            .search_vector("embedding", [1.0, 0.0, 0.0], top_k=2, approximate=options)
            .all()
        )
        print("approximate search (fallback=exact):")
        for row in rows:
            print(f"  id={row.id} score={search_scores(row).score}")

        # The boolean form opts in with server defaults; a dict is also accepted.
        rows_default = (
            await client.search(Item)
            .search_vector("embedding", [1.0, 0.0, 0.0], top_k=2, approximate=True)
            .all()
        )
        print(f"approximate (defaults): ids={[r.id for r in rows_default]}")


if __name__ == "__main__":
    asyncio.run(main())
