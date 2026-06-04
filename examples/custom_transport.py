"""Inject a custom transport / pre-seeded reference server.

Transports are pluggable: a client accepts any :class:`~aura.transport.base.Transport`.
Here we pre-seed an in-memory reference server and hand it to the client, which is also
exactly how the test suite injects deterministic state.

Run: ``python examples/custom_transport.py``
"""

from __future__ import annotations

import asyncio

from aura import Field, Model
from aura.client import Client
from aura.config import parse_dsn
from aura.schema import schema_document
from aura.transport.memory import MemoryTransport, ReferenceServer


class Sensor(Model):
    id: int = Field(primary_key=True)
    location: str
    reading: float


async def main() -> None:
    # Build and pre-seed a reference server.
    server = ReferenceServer()
    server.load_schema(schema_document([Sensor])["models"])
    server._tables["Sensor"] = {  # direct seeding for the example
        1: {"id": 1, "location": "roof", "reading": 21.5},
        2: {"id": 2, "location": "lab", "reading": 19.2},
    }

    config = parse_dsn("aura+memory://localhost/iot")
    transport = MemoryTransport(server)
    client = Client(config, transport, [Sensor])
    await client._open()
    try:
        sensors = await client.query(Sensor).order_by(Sensor.id.asc()).all()
        for sensor in sensors:
            print(f"sensor {sensor.id} @ {sensor.location}: {sensor.reading}")
        print("transport stats:", transport.stats.as_dict())
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
