"""MySQL/MariaDB backend: the same typed API over a production SQL database.

Set ``AURA_TEST_MYSQL_DSN`` (or ``MYSQL_DSN``), for example
``mysql://root:password@localhost:3306/aura_test``. With no DSN the example prints
instructions and exits cleanly.

Run: ``python examples/mysql_backend.py`` (requires ``pip install aura-connector[mysql]``)
Start a local server: ``docker compose -f docker-compose.backends.yml up -d mysql``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, Field, Model


class Product(Model):
    id: int = Field(primary_key=True)
    sku: str = Field(unique=True, index=True)
    price_cents: int = Field(default=0)


async def main() -> None:
    dsn = os.environ.get("AURA_TEST_MYSQL_DSN") or os.environ.get("MYSQL_DSN")
    if not dsn:
        print(
            "No MySQL DSN found. Set AURA_TEST_MYSQL_DSN, e.g.\n"
            "  export AURA_TEST_MYSQL_DSN=mysql://root:password@localhost:3306/aura_test\n"
            "Then: pip install aura-connector[mysql] and re-run."
        )
        return

    async with Aura.connect(dsn, models=[Product]) as db:
        # Tables for the registered models are created on connect.
        print("connected:", db.backend.name)
        await db.upsert(
            Product, key={Product.id: 1}, values={Product.sku: "A-1", Product.price_cents: 999}
        )
        rows = await db.query(Product).where(Product.price_cents > 0).all()
        print("products:", [(p.sku, p.price_cents) for p in rows])


if __name__ == "__main__":
    asyncio.run(main())
