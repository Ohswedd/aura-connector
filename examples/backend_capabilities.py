"""Inspect and compare backend capabilities.

Aura represents feature parity honestly: each backend declares what it supports through a
:class:`~aura.backends.capabilities.BackendCapabilities` value, and the client raises a
structured error rather than emulating a missing feature. This example connects to the
memory and SQLite backends and prints their capability matrix side by side.

Run: ``python examples/backend_capabilities.py`` (requires ``pip install aura-connector[sqlite]``)
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model
from aura.backends.capabilities import CAPABILITY_FLAGS
from aura.errors import AuraBackendCapabilityError


class Point(Model):
    id: int = Field(primary_key=True)
    label: str


async def main() -> None:
    async with (
        Aura.connect("aura+memory://localhost/app", models=[Point]) as memory,
        Aura.connect("sqlite://", models=[Point]) as sqlite,
    ):
        mem_caps = memory.capabilities()
        sql_caps = sqlite.capabilities()
        print(f"{'capability':<22}{'memory':<10}{'sqlite':<10}")
        for flag in CAPABILITY_FLAGS:
            print(f"{flag:<22}{mem_caps.supports(flag)!s:<10}{sql_caps.supports(flag)!s:<10}")

        # Asking SQLite for vector search raises a structured capability error.
        await sqlite.insert(Point(id=1, label="origin"))
        try:
            await sqlite.search(Point).nearest(Point.label, [0.0, 0.0]).all()
        except AuraBackendCapabilityError as exc:
            print("\nexpected:", exc.code, "-", exc.context.get("capability"))


if __name__ == "__main__":
    asyncio.run(main())
