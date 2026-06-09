"""Exact and approximate vector search against AuraDB v1.2.0 (connector v0.6.0).

`search_vector` is exact nearest-neighbour by default — the correctness baseline.
Pass ``approximate=True`` (or a dict of HNSW parameters ``m`` / ``ef_construction``
/ ``ef_search``) to opt into AuraDB's approximate (HNSW) **preview** for a query.
This is not production ANN; exact search remains the default.

Run: ``python examples/auradb_vector_options.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, Vector, search_scores


class Item(Model):
    id: int = Field(primary_key=True)
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/vectors", models=[Item]) as client:
        await client.bulk_insert(
            Item,
            [
                Item(id=1, embedding=[1.0, 0.0, 0.0]),
                Item(id=2, embedding=[0.0, 1.0, 0.0]),
                Item(id=3, embedding=[0.9, 0.1, 0.0]),
            ],
        )
        query = [1.0, 0.0, 0.0]

        # Exact search (the default and correctness baseline).
        exact = await client.search(Item).search_vector("embedding", query, top_k=2).all()
        print("exact:", [(r.id, round(search_scores(r).score or 0.0, 3)) for r in exact])

        # Opt-in approximate (HNSW) preview with the default parameters.
        approx = await (
            client.search(Item).search_vector("embedding", query, top_k=2, approximate=True).all()
        )
        print("approximate (preview):", [r.id for r in approx])

        # Tuned beam width: higher ef_search trades cost for recall.
        tuned = await (
            client.search(Item)
            .search_vector("embedding", query, top_k=2, approximate={"ef_search": 64})
            .all()
        )
        print("approximate (ef_search=64):", [r.id for r in tuned])


if __name__ == "__main__":
    asyncio.run(main())
