"""Clear, typed errors for invalid or unsupported search.

The connector validates ranked-search queries client-side and raises typed errors
rather than sending a malformed or unsupported request:

- ``AuraQueryError`` for invalid arguments (empty query, bad weights, non-positive
  ``top_k``, unknown rank/fusion);
- ``AuraCapabilityError`` when the backend/server does not support a requested
  feature.

Run: python examples/auradb_search_errors.py
"""

from __future__ import annotations

import asyncio

from aura import Aura, AuraCapabilityError, Field, Model, Vector
from aura.errors import AuraQueryError


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str = Field(full_text=True)
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/search", models=[Doc]) as client:
        # 1. Invalid arguments are rejected before any request is sent.
        for label, build in (
            ("empty query", lambda: client.search(Doc).search_text("body", "   ")),
            (
                "both weights zero",
                lambda: client.search(Doc).search_hybrid(
                    "body", "x", "embedding", [1.0, 0.0, 0.0], weights=(0.0, 0.0)
                ),
            ),
            (
                "non-positive top_k",
                lambda: client.search(Doc).search_vector("embedding", [1.0, 0.0, 0.0], top_k=0),
            ),
        ):
            try:
                build()
                print(f"  {label}: NOT rejected (unexpected)")
            except AuraQueryError as exc:
                print(f"  {label}: AuraQueryError -> {exc}")

        # 2. Capability errors carry the backend and the missing capability.
        caps = client.capabilities()
        print("hybrid supported:", caps.supports("hybrid_search"))
        try:
            # The reference engine supports hybrid; against a backend that does not
            # (SQL/Mongo/Redis), this raises AuraCapabilityError with context.
            if not caps.supports("hybrid_search"):
                raise AuraCapabilityError("hybrid not supported by this backend")
        except AuraCapabilityError as exc:
            print("  capability error context:", exc.context)


if __name__ == "__main__":
    asyncio.run(main())
