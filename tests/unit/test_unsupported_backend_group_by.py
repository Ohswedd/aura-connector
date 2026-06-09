"""A backend lacking ``group_by`` rejects a group-by aggregation cleanly.

Aura never silently drops an unsupported clause; it raises ``AuraCapabilityError``
naming the backend and the missing capability.
"""

from __future__ import annotations

from typing import Any

import pytest

from aura import AuraModel, Field
from aura.backends.capabilities import BackendCapabilities
from aura.client import Client
from aura.config import parse_dsn
from aura.errors import AuraCapabilityError


class Product(AuraModel):
    id: int = Field(primary_key=True)
    category: str


class _NoGroupByBackend:
    """A minimal opened backend that supports search but not group-by/profile."""

    name = "legacy_auradb"

    def capabilities(self) -> BackendCapabilities:
        # Search flags on so the builder's search guard passes; the v1.3.0 flags
        # stay off so the explicit feature gate is what fires.
        return BackendCapabilities(
            name=self.name,
            vector_search=True,
            hybrid_search=True,
            full_text_search=True,
        )

    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> Any:  # pragma: no cover
        raise AssertionError("capability gate should fire before execution")


def _opened_client(backend: Any) -> Client:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, backend=backend)
    client._opened = True
    return client


async def test_group_by_raises_capability_error() -> None:
    client = _opened_client(_NoGroupByBackend())
    with pytest.raises(AuraCapabilityError) as exc:
        await client.query(Product).group_by("category").aggregate()
    assert client.backend.name in str(exc.value)
    assert exc.value.context.get("capability") == "group_by"


async def test_profile_raises_capability_error() -> None:
    client = _opened_client(_NoGroupByBackend())
    with pytest.raises(AuraCapabilityError) as exc:
        await client.query(Product).aggregate_count().profile().aggregate()
    assert exc.value.context.get("capability") == "query_profile"
