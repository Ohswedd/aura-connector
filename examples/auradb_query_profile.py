"""Best-effort query profiling against AuraDB v1.3.0 (connector v0.7.0).

`query.profile()` opts a read into an advisory `QueryProfile` the server attaches
to the result when it can (planning/execution timing, rows scanned/matched,
search mode, and more). Every field is optional: treat the profile as a
diagnostic aid, not a stable contract. The example reads the profile off an
aggregation against the in-memory reference engine.

Run: ``python examples/auradb_query_profile.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model


class Product(Model):
    id: int = Field(primary_key=True)
    category: str = Field(index=True)
    price: int
    body: str


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/profile", models=[Product]) as client:
        await client.bulk_insert(
            Product,
            [
                Product(id=0, category="a", price=10, body="red running shoe"),
                Product(id=1, category="a", price=20, body="blue running shoe"),
                Product(id=2, category="b", price=30, body="winter boot"),
                Product(id=3, category="b", price=40, body="rain boot"),
            ],
        )

        result = (
            await client.query(Product)
            .search_text("body", "shoe")
            .aggregate_count()
            .facet("category")
            .profile()
            .aggregate()
        )
        print(f"matched={result.matched}")
        profile = result.profile
        if profile is None:
            # An older server may not attach a profile; the code stays correct.
            print("server attached no profile (advisory feature)")
            return
        print("query profile (advisory; fields are optional):")
        print(f"  rows_scanned={profile.rows_scanned} rows_matched={profile.rows_matched}")
        print(f"  search_mode={profile.search_mode} index_used={profile.index_used}")
        print(f"  facet_buckets={profile.facet_buckets}")
        if profile.warnings:
            print(f"  warnings={profile.warnings}")


if __name__ == "__main__":
    asyncio.run(main())
