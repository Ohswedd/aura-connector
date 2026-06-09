"""Invariant: the default transaction isolation remains snapshot in v0.7.0.

AuraDB provides snapshot isolation with optimistic conflict detection; the
connector never overclaims serializable. This guard fails loudly if a future
change shifts the default.
"""

from __future__ import annotations

from aura.client import DEFAULT_ISOLATION, Client, _normalize_isolation
from aura.config import parse_dsn
from aura.transport.memory import MemoryTransport, ReferenceServer


def test_default_isolation_constant_is_snapshot() -> None:
    assert DEFAULT_ISOLATION == "snapshot"
    assert DEFAULT_ISOLATION != "serializable"


def test_snapshot_synonym_normalizes_to_snapshot() -> None:
    assert _normalize_isolation("snapshot") == "snapshot"
    assert _normalize_isolation("snapshot_isolation") == "snapshot"


async def test_transaction_default_uses_snapshot() -> None:
    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()))
    await client._open()
    try:
        tx = client.transaction()
        assert tx._isolation == "snapshot"
    finally:
        await client.close()
