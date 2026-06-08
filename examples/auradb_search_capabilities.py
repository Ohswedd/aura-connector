"""Capability negotiation for AuraDB search features.

The connector reads the server's advertised capabilities at handshake (against the
native AuraDB backend) and exposes them via ``client.capabilities()``. Ranked
search is gated on what the server actually implements, so a query against a server
that lacks a feature raises ``AuraCapabilityError`` instead of silently returning
wrong results.

Run against the in-process reference engine (which implements BM25 + hybrid):
    python examples/auradb_search_capabilities.py
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, Vector


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str = Field(full_text=True)
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/search", models=[Doc]) as client:
        caps = client.capabilities()
        print("backend:", caps.name)
        for feature in ("full_text_search", "vector_search", "hybrid_search"):
            print(f"  {feature}: {caps.supports(feature)}")

        # Branch on capabilities rather than catching errors after the fact.
        if caps.supports("hybrid_search"):
            await client.insert(Doc(id=1, body="raft consensus", embedding=[1.0, 0.0, 0.0]))
            rows = await (
                client.search(Doc)
                .search_hybrid("body", "raft", "embedding", [1.0, 0.0, 0.0], top_k=3)
                .all()
            )
            print("hybrid results:", [d.id for d in rows])
        else:
            print("server does not support hybrid search; skipping")


if __name__ == "__main__":
    asyncio.run(main())
