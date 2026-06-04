"""Redis backend: limited key-value / cache use through the typed API.

Set ``AURA_TEST_REDIS_DSN`` (or ``REDIS_DSN``), for example ``redis://localhost:6379/0``.
With no DSN the example prints instructions and exits cleanly. Redis is intentionally
limited: primary-key access, existence, and prefix counts are supported; relational filters,
joins, and vector search are not, and raise a structured capability error.

Run: ``python examples/redis_backend.py`` (requires ``pip install aura-connector[redis]``)
Start a local server: ``docker compose -f docker-compose.backends.yml up -d redis``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, Field, Model
from aura.errors import AuraBackendCapabilityError


class Session(Model):
    id: str = Field(primary_key=True)
    user: str
    data: dict[str, str] = Field(default_factory=dict)


async def main() -> None:
    dsn = os.environ.get("AURA_TEST_REDIS_DSN") or os.environ.get("REDIS_DSN")
    if not dsn:
        print(
            "No Redis DSN found. Set AURA_TEST_REDIS_DSN, e.g.\n"
            "  export AURA_TEST_REDIS_DSN=redis://localhost:6379/0\n"
            "Then: pip install aura-connector[redis] and re-run."
        )
        return

    async with Aura.connect(dsn, models=[Session]) as db:
        print("connected:", db.backend.name, "(key-value, limited)")
        await db.insert(Session(id="s1", user="ada", data={"theme": "dark"}))
        got = await db.Session.find(id="s1")
        print("fetched by key:", got.user, got.data)

        # Relational filtering is unsupported on Redis and says so clearly.
        try:
            await db.query(Session).where(Session.user == "ada").all()
        except AuraBackendCapabilityError as exc:
            print("expected:", exc.code)

        await db.delete(Session).where(Session.id == "s1").execute()


if __name__ == "__main__":
    asyncio.run(main())
