"""Inspecting a search query plan from the connector.

``QueryBuilder.explain()`` returns the client-side Query IR that the connector
will send to AuraDB — useful for verifying which ranked-retrieval clause a query
builds (text_search, vector, or hybrid) before executing it.

Server-side EXPLAIN ANALYZE (with measured candidate counts, ranking mode, and
timing) is produced by the AuraDB server itself; run it with the server CLI:

    auradb search explain --input query.json --analyze

Run this example: ``python examples/auradb_explain_analyze.py``
"""

from __future__ import annotations

import asyncio
import json

from aura import Aura, Field, Model, Vector


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/search", models=[Doc]) as client:
        await client.insert(Doc(id=1, body="raft consensus", embedding=[1.0, 0.0, 0.0]))

        text_plan = client.search(Doc).search_text("body", "raft").explain()
        print("text_search IR:")
        print(json.dumps(text_plan["text_search"], indent=2))

        hybrid_plan = (
            client.search(Doc)
            .search_hybrid("body", "raft", "embedding", [1.0, 0.0, 0.0], top_k=5)
            .explain()
        )
        print("hybrid IR:")
        print(json.dumps(hybrid_plan["hybrid"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
