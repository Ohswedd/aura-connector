"""Vector and hybrid search with typed fixed-dimension vectors.

Run: ``python examples/vector_search.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, Vector


class Chunk(Model):
    id: int = Field(primary_key=True)
    title: str
    body: str
    embedding: Vector[3] = Field(vector_index="hnsw")


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/rag", models=[Chunk]) as client:
        await client.bulk_insert(
            Chunk,
            [
                Chunk(
                    id=1,
                    title="Refund policy",
                    body="how to get a refund",
                    embedding=[1.0, 0.0, 0.0],
                ),
                Chunk(id=2, title="Shipping", body="delivery times", embedding=[0.0, 1.0, 0.0]),
                Chunk(id=3, title="Returns", body="return an item", embedding=[0.9, 0.1, 0.0]),
            ],
        )

        # Pure vector nearest-neighbour search.
        query_vector = [1.0, 0.0, 0.0]
        matches = await (
            client.search(Chunk)
            .nearest(Chunk.embedding, query_vector, metric="cosine")
            .limit(2)
            .all()
        )
        print("vector matches:")
        for chunk in matches:
            print(f"  {chunk.title}  score={chunk.__score__:.3f}")

        # Hybrid lexical + vector search with score fusion.
        hybrid = await (
            client.search(Chunk)
            .text(Chunk.title, Chunk.body, query="refund")
            .similar_to(Chunk.embedding, query_vector)
            .fusion(alpha=0.6)
            .limit(3)
            .all()
        )
        print("hybrid matches:", [c.title for c in hybrid])


if __name__ == "__main__":
    asyncio.run(main())
