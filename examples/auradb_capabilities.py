"""Branch on backend capabilities with require()/supports()/describe().

A backend honestly declares what it can and cannot do. ``client.capabilities()``
returns that declaration; use ``supports`` to branch, ``require`` to assert a hard
dependency (raising a clear error when missing), and ``describe`` to print a
summary. The connector never silently emulates a feature a backend lacks, so a
non-AuraDB backend will not claim AuraDB-specific analytics.

Run: ``python examples/auradb_capabilities.py``
"""

from __future__ import annotations

import asyncio

from aura import Aura
from aura.errors import AuraBackendCapabilityError


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/app") as client:
        caps = client.capabilities()

        summary = caps.describe()
        print(f"backend: {summary['name']}")
        print(f"supported: {summary['supported']}")

        # Soft branch: only run the profile path if the backend supports it.
        if caps.supports("query_profile"):
            print("query_profile is available")
        else:
            print("query_profile is not available on this backend")

        # Hard requirement: fail fast with an actionable error if missing.
        try:
            caps.require("hnsw_preview")
            print("hnsw_preview is available")
        except AuraBackendCapabilityError as err:
            print(f"missing required capability: {err.context['capability']}")


if __name__ == "__main__":
    asyncio.run(main())
