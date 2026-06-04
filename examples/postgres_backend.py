"""PostgreSQL backend: the same typed API over a production SQL database.

Set ``AURA_TEST_POSTGRES_DSN`` (or ``POSTGRES_DSN``) to a reachable PostgreSQL instance, for
example ``postgresql://postgres:postgres@localhost:5432/aura_test``. With no DSN the example
prints how to provide one and exits cleanly, so it is safe to run anywhere.

Run: ``python examples/postgres_backend.py`` (requires ``pip install aura-connector[postgres]``)
Start a local server: ``docker compose -f docker-compose.backends.yml up -d postgres``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, Field, Model


class Customer(Model):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)
    credits: int = Field(default=0)


async def main() -> None:
    dsn = os.environ.get("AURA_TEST_POSTGRES_DSN") or os.environ.get("POSTGRES_DSN")
    if not dsn:
        print(
            "No PostgreSQL DSN found. Set AURA_TEST_POSTGRES_DSN, e.g.\n"
            "  export AURA_TEST_POSTGRES_DSN="
            "postgresql://postgres:postgres@localhost:5432/aura_test\n"
            "Then: pip install aura-connector[postgres] and re-run."
        )
        return

    async with Aura.connect(dsn, models=[Customer]) as db:
        # Tables for the registered models are created on connect.
        print("connected:", db.backend.name)
        await db.upsert(
            Customer, key={Customer.id: 1}, values={Customer.name: "Acme", Customer.credits: 10}
        )
        top = await db.query(Customer).order_by(Customer.credits.desc()).limit(5).all()
        print("top customers:", [(c.name, c.credits) for c in top])


if __name__ == "__main__":
    asyncio.run(main())
