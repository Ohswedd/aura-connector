"""Observability: per-operation metrics, latency percentiles, and query fingerprints.

Demonstrates the observability layer — no external collector required. The same
``telemetry={...}`` argument enables OpenTelemetry spans when that package is installed.

Run: ``python examples/observability.py``
"""

from __future__ import annotations

import asyncio
import json

from aura import Client, Field, Model, query_fingerprint


class Product(Model):
    id: int = Field(primary_key=True)
    name: str
    price_cents: int = Field(index=True)


async def main() -> None:
    async with Client.connect(
        "aura+memory://localhost/shop",
        models=[Product],
        telemetry={"opentelemetry": True, "capture_query_ir": "redacted"},
    ) as client:
        await client.bulk_insert(
            Product,
            [Product(id=i, name=f"item-{i}", price_cents=100 * i) for i in range(1, 21)],
        )
        for _ in range(5):
            await client.query(Product).filter(Product.price_cents > 500).all()
        await client.query(Product).count()

        snapshot = client.metrics.snapshot()
        print("Metrics snapshot:")
        print(json.dumps(snapshot, indent=2))

        # Two queries that differ only in their literal bound value share a fingerprint,
        # and the fingerprint never contains the value itself.
        ir_a = client.query(Product).filter(Product.price_cents > 500).explain()
        ir_b = client.query(Product).filter(Product.price_cents > 999).explain()
        print("\nfingerprint(price > 500):", query_fingerprint(ir_a))
        print("fingerprint(price > 999):", query_fingerprint(ir_b))
        assert query_fingerprint(ir_a) == query_fingerprint(ir_b)


if __name__ == "__main__":
    asyncio.run(main())
