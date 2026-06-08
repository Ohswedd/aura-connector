"""Hybrid text-plus-vector search against AuraDB v1.1.0.

Hybrid search fuses BM25 text relevance with exact vector similarity. Exact
vector search remains the correctness baseline; approximate (ANN) search is not
implemented in AuraDB v1.1.0.

Run: ``python examples/auradb_hybrid_search.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, AuraCapabilityError, Field, Model, Vector, search_scores


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/search", models=[Doc]) as client:
        # Capability negotiation: fail clearly if the backend lacks hybrid search.
        if not client.capabilities().supports("hybrid_search"):
            raise AuraCapabilityError("backend does not support hybrid search")

        await client.bulk_insert(
            Doc,
            [
                Doc(id=1, body="alpha alpha alpha", embedding=[0.0, 0.0, 1.0]),
                Doc(id=2, body="unrelated words", embedding=[1.0, 0.0, 0.0]),
                Doc(id=3, body="alpha context", embedding=[0.9, 0.1, 0.0]),
            ],
        )

        rows = await (
            client.search(Doc)
            .search_hybrid(
                "body",
                "alpha",
                "embedding",
                [1.0, 0.0, 0.0],
                weights=(0.5, 0.5),
                fusion="weighted_sum",
                top_k=3,
            )
            .all()
        )
        print("Hybrid (weighted_sum) results:")
        for doc in rows:
            s = search_scores(doc)
            print(
                f"  #{s.rank} id={doc.id} fused={s.score:.3f} "
                f"text={s.text_score} vector={s.vector_score}"
            )

        # Reciprocal rank fusion is robust to score-scale differences.
        rrf = await (
            client.search(Doc)
            .search_hybrid(
                "body",
                "alpha",
                "embedding",
                [1.0, 0.0, 0.0],
                fusion="reciprocal_rank_fusion",
                top_k=3,
            )
            .all()
        )
        print("Hybrid (RRF) top id:", rrf[0].id if rrf else None)


if __name__ == "__main__":
    asyncio.run(main())
