"""Basic CRUD with Aura against the in-memory reference server.

Run: ``python examples/basic_crud.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model


class Product(Model):
    id: int = Field(primary_key=True)
    name: str
    price_cents: int


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/shop", models=[Product]) as client:
        # Create
        await client.insert(Product(id=1, name="Keyboard", price_cents=4999))
        await client.bulk_insert(
            Product,
            [
                Product(id=2, name="Mouse", price_cents=2999),
                Product(id=3, name="Monitor", price_cents=19999),
            ],
        )

        # Read
        keyboard = await client.Product.find(id=1)
        print("found:", keyboard.name, keyboard.price_cents)

        cheap = await (
            client.query(Product)
            .where(Product.price_cents < 5000)
            .order_by(Product.price_cents.asc())
            .all()
        )
        print("under $50:", [p.name for p in cheap])

        # Update
        await client.update(Product).where(Product.id == 2).set(price_cents=2499).execute()
        print("updated mouse:", (await client.Product.find(id=2)).price_cents)

        # Count / exists
        print("total products:", await client.query(Product).count())
        print("has monitor:", await client.query(Product).where(Product.id == 3).exists())

        # Delete
        await client.delete(Product).where(Product.id == 3).execute()
        print("after delete:", await client.query(Product).count())


if __name__ == "__main__":
    asyncio.run(main())
