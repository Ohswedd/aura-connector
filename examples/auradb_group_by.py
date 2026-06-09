"""Group-by aggregation against AuraDB v1.3.0 (connector v0.7.0).

`query.group_by(field, limit=...)` groups an `.aggregate()` query by a scalar
field and computes its metrics per group. The result carries a typed
`GroupByResult` on `AggregateResult.groups`, ordered count-descending then
key-ascending, with `group_count_total` so you can detect truncation.

Run: ``python examples/auradb_group_by.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model


class Order(Model):
    id: int = Field(primary_key=True)
    region: str = Field(index=True)
    amount_cents: int


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/group_by", models=[Order]) as client:
        await client.bulk_insert(
            Order,
            [
                Order(id=0, region="emea", amount_cents=1000),
                Order(id=1, region="emea", amount_cents=3000),
                Order(id=2, region="emea", amount_cents=2000),
                Order(id=3, region="amer", amount_cents=5000),
                Order(id=4, region="amer", amount_cents=1500),
                Order(id=5, region="apac", amount_cents=4000),
            ],
        )

        # Group orders by region, with count + min/max amount per group.
        result = (
            await client.query(Order)
            .group_by("region")
            .aggregate_count()
            .min("amount_cents")
            .max("amount_cents")
            .aggregate()
        )
        groups = result.groups
        assert groups is not None
        print(f"grouped by {groups.field!r}: {groups.group_count_total} distinct group(s)")
        for group in groups.groups:
            print(
                f"  {group.key!r}: count={group.count} "
                f"min={group.metric('min', 'amount_cents')} "
                f"max={group.metric('max', 'amount_cents')}"
            )

        # A bounded group-by reports truncation via group_count_total.
        top = await client.query(Order).group_by("region", limit=2).aggregate()
        assert top.groups is not None
        print(f"top-2 groups (truncated={top.groups.truncated}):")
        for group in top.groups.groups:
            print(f"  {group.key!r}: {group.count}")


if __name__ == "__main__":
    asyncio.run(main())
