"""Invariant: ``"serializable"`` stays a deprecated alias mapping to snapshot.

The legacy token is accepted for backward compatibility but never propagated
verbatim and never upgrades AuraDB's snapshot semantics.
"""

from __future__ import annotations

from aura.client import _ISOLATION_ALIASES, Client, _normalize_isolation
from aura.config import parse_dsn
from aura.transport.memory import MemoryTransport, ReferenceServer


def test_serializable_normalizes_to_snapshot() -> None:
    assert _normalize_isolation("serializable") == "snapshot"


def test_serializable_alias_is_present_and_maps_to_snapshot() -> None:
    assert _ISOLATION_ALIASES["serializable"] == "snapshot"


async def test_transaction_with_serializable_token_runs_under_snapshot() -> None:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()))
    await client._open()
    try:
        tx = client.transaction(isolation="serializable")
        # The alias is mapped, not propagated verbatim.
        assert tx._isolation == "snapshot"
    finally:
        await client.close()
