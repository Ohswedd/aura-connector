"""Aggregations and terms facets against AuraDB v1.2.0 (connector v0.6.0).

`query.facet(...)`, `query.aggregate_count()`, `query.min(...)`, `query.max(...)`,
then `await query.aggregate()` return a typed `AggregateResult`. A `search_text`
clause scopes the metrics/facets to the BM25 candidate set (a "search facet").

Run: ``python examples/auradb_facets.py``
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
    async with Aura.connect("aura+memory://localhost/facets", models=[Product]) as client:
        await client.bulk_insert(
            Product,
            [
                Product(id=0, category="a", price=10, body="red running shoe"),
                Product(id=1, category="a", price=20, body="blue running shoe"),
                Product(id=2, category="a", price=30, body="running shoe laces"),
                Product(id=3, category="b", price=40, body="winter boot"),
                Product(id=4, category="b", price=50, body="rain boot"),
                Product(id=5, category="c", price=60, body="wool sock"),
            ],
        )

        # Count + min/max + a terms facet over the whole collection.
        result = (
            await client.query(Product)
            .aggregate_count()
            .min("price")
            .max("price")
            .facet("category")
            .aggregate()
        )
        print(f"matched={result.matched} count={result.metric('count')}")
        print(f"price min={result.metric('min', 'price')} max={result.metric('max', 'price')}")
        for bucket in result.facet("category").buckets:
            print(f"  category {bucket.value!r}: {bucket.count}")

        # A BM25 search facet: aggregate only the "running" candidate set.
        scoped = (
            await client.query(Product)
            .search_text("body", "running")
            .aggregate_count()
            .facet("category")
            .aggregate()
        )
        print(f"search-scoped matched={scoped.matched} (search_scoped={scoped.search_scoped})")


if __name__ == "__main__":
    asyncio.run(main())
