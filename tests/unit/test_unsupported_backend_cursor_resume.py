"""A backend lacking ``cursor_resume`` rejects a public cursor resume cleanly."""

from __future__ import annotations

from typing import Any

import pytest

from aura import AuraModel, Field
from aura.backends.capabilities import BackendCapabilities
from aura.client import Client
from aura.config import parse_dsn
from aura.errors import AuraCapabilityError


class Doc(AuraModel):
    id: int = Field(primary_key=True)
    body: str


class _NoCursorResumeBackend:
    name = "legacy_auradb"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            vector_search=True,
            hybrid_search=True,
            full_text_search=True,
        )

    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> Any:  # pragma: no cover
        raise AssertionError("capability gate should fire before execution")


def _opened_client() -> Client:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, backend=_NoCursorResumeBackend())
    client._opened = True
    return client


async def test_resume_search_raises_capability_error() -> None:
    client = _opened_client()
    search = client.search(Doc).search_text("body", "alpha")
    with pytest.raises(AuraCapabilityError) as exc:
        await client.resume_search(search, "opaque-token", page_size=2)
    assert client.backend.name in str(exc.value)
    assert exc.value.context.get("capability") == "cursor_resume"


async def test_builder_page_raises_capability_error() -> None:
    client = _opened_client()
    with pytest.raises(AuraCapabilityError) as exc:
        await client.search(Doc).search_text("body", "alpha").page(page_size=2, cursor="tok")
    assert exc.value.context.get("capability") == "cursor_resume"
