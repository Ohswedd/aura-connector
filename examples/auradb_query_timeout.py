"""Per-query timeouts against AuraDB v1.2.0 (connector v0.6.0).

`.timeout(milliseconds)` sets a per-query execution deadline. AuraDB v1.2.0
enforces it cooperatively and returns a structured ``query_timeout`` error
(surfaced as ``AuraTimeoutError``) if a read exceeds the budget, while the
connection stays usable. A per-query timeout may only *lower* the server's
configured maximum (``[limits] max_query_time_ms``), never raise it.

Run: ``python examples/auradb_query_timeout.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model


class Event(Model):
    id: int = Field(primary_key=True)
    name: str


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/timeout", models=[Event]) as client:
        await client.bulk_insert(
            Event,
            [Event(id=i, name=f"event-{i}") for i in range(50)],
        )

        # A generous budget: the query completes well within it.
        rows = await client.query(Event).timeout(5_000).all()
        print(f"returned {len(rows)} rows within the 5s deadline")

        # The deadline rides on every read shape (find / count / search).
        count = await client.query(Event).timeout(5_000).count()
        print(f"count within deadline: {count}")


if __name__ == "__main__":
    asyncio.run(main())
