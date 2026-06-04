"""Benchmark: end-to-end round trip through the reference transport."""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aura import AuraModel, Field
from aura.client import Client


class Item(AuraModel):
    id: int = Field(primary_key=True)
    name: str


async def run() -> None:
    client = await Client.connect("aura+memory://localhost/bench", models=[Item])
    try:
        await client.bulk_insert(Item, [Item(id=i, name=f"i{i}") for i in range(1000)])

        async def point_read() -> None:
            await client.Item.find(id=500)

        # warm up
        for _ in range(100):
            await point_read()

        samples: list[float] = []
        for _ in range(2000):
            start = time.perf_counter()
            await point_read()
            samples.append((time.perf_counter() - start) * 1_000_000)
        samples.sort()
        p50 = statistics.median(samples)
        p95 = samples[int(len(samples) * 0.95) - 1]
        print("Reference round-trip benchmark (real measured timings):")
        print(f"point_read_through_protocol      p50={p50:8.3f}us  p95={p95:8.3f}us")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
