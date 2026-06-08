"""Unit tests for client lifecycle and error mapping."""

from __future__ import annotations

import pathlib

import pytest

from aura import AuraModel, Field
from aura.client import DEFAULT_ISOLATION, Client, _normalize_isolation
from aura.config import parse_dsn
from aura.errors import AuraClientClosedError, AuraConnectionError, AuraQueryError
from aura.transport.memory import MemoryTransport, ReferenceServer


class Gadget(AuraModel):
    id: int = Field(primary_key=True)
    name: str


def make_client() -> Client:
    config = parse_dsn("aura+memory://localhost/test")
    return Client(config, MemoryTransport(ReferenceServer()), [Gadget])


async def test_connect_via_context_manager() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        assert await client.ping() is True


async def test_connect_via_await() -> None:
    client = await Client.connect("aura+memory://localhost/test", models=[Gadget])
    try:
        assert await client.ping() is True
    finally:
        await client.close()


async def test_use_before_open_raises() -> None:
    client = make_client()
    with pytest.raises(AuraConnectionError):
        await client.ping()


async def test_use_after_close_raises() -> None:
    client = make_client()
    await client._open()
    await client.close()
    with pytest.raises(AuraClientClosedError):
        await client.ping()


async def test_close_is_idempotent() -> None:
    client = make_client()
    await client._open()
    await client.close()
    await client.close()  # no error


async def test_dynamic_model_access() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        await client.insert(Gadget(id=1, name="a"))
        got = await client.Gadget.find(id=1)
        assert got.name == "a"


async def test_unknown_attribute_raises() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        with pytest.raises(AttributeError):
            _ = client.NotAModel


async def test_health_snapshot() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        health = await client.health()
        assert health["status"] == "ok"
        assert "Gadget" in health["models"]
        assert health["protocol_version"] == 1


async def test_server_error_is_mapped() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        # duplicate insert triggers a constraint error from the reference server
        await client.insert(Gadget(id=1, name="a"))
        with pytest.raises(AuraQueryError):
            await client.raw("DROP TABLE Gadget")  # unsupported raw -> query error


def test_error_code_map_covers_every_typed_error() -> None:
    """Every server error code maps to its specific exception class."""
    from aura.client import _ERROR_CODE_MAP
    from aura.errors import (
        AuraAuthenticationError,
        AuraAuthorizationError,
        AuraConstraintError,
        AuraNotFoundError,
        AuraNotLeaderError,
        AuraProtocolError,
        AuraSchemaError,
        AuraServerError,
        AuraValidationError,
    )
    from aura.protocol.frames import Frame
    from aura.protocol.messages import ErrorBody, encode_body
    from aura.protocol.opcodes import Opcode

    expected = {
        "validation_error": AuraValidationError,
        "query_error": AuraQueryError,
        "schema_error": AuraSchemaError,
        "not_found": AuraNotFoundError,
        "constraint_violation": AuraConstraintError,
        "authentication_error": AuraAuthenticationError,
        "authorization_error": AuraAuthorizationError,
        "not_leader": AuraNotLeaderError,
        "protocol_error": AuraProtocolError,
        "server_error": AuraServerError,
    }
    assert expected == _ERROR_CODE_MAP

    client = make_client()
    # An unknown code falls back to AuraServerError; known codes map precisely.
    for code, cls in [*expected.items(), ("totally_unknown", AuraServerError)]:
        body = ErrorBody(code=code, message="boom", retryable=False, context={"k": "v"})
        frame = Frame(opcode=Opcode.ERROR, payload=encode_body(body.to_payload()), request_id=5)
        with pytest.raises(cls) as info:
            client._raise_error(frame)
        assert info.value.request_id == 5
        assert info.value.context == {"k": "v"}


async def test_metrics_are_collected() -> None:
    async with Client.connect("aura+memory://localhost/test", models=[Gadget]) as client:
        await client.insert(Gadget(id=1, name="a"))
        await client.query(Gadget).all()
        await client.query(Gadget).count()
        snap = client.metrics.snapshot()
        assert snap["query_count"]["insert"] == 1
        assert snap["query_count"]["select"] == 1
        assert snap["query_count"]["count"] == 1
        assert snap["request_latency"]["count"] == 3
        assert snap["serialize"]["count"] == 3
        assert snap["bytes_sent"] > 0
        assert snap["bytes_received"] > 0


async def test_telemetry_option_without_otel_is_noop() -> None:
    # Enabling telemetry must not error even when opentelemetry is absent.
    async with Client.connect(
        "aura+memory://localhost/test", models=[Gadget], telemetry={"opentelemetry": True}
    ) as client:
        await client.insert(Gadget(id=1, name="a"))
        assert client.metrics.snapshot()["query_count"]["insert"] == 1


# --------------------------------------------------------------------------- #
# Transaction isolation: AuraDB is snapshot isolation, never serializable.    #
# --------------------------------------------------------------------------- #
async def test_transaction_default_is_snapshot_not_serializable() -> None:
    # The default isolation must reflect AuraDB's actual guarantee (snapshot
    # isolation), not overclaim serializable.
    assert DEFAULT_ISOLATION == "snapshot"
    client = make_client()
    await client._open()
    try:
        tx = client.transaction()
        assert tx._isolation == "snapshot"
        assert tx._isolation != "serializable"
    finally:
        await client.close()


async def test_transaction_serializable_alias_maps_to_snapshot_if_kept() -> None:
    # The legacy "serializable" token is still accepted (no abrupt break) but is
    # normalized to snapshot isolation; "snapshot_isolation" is a synonym too.
    assert _normalize_isolation("serializable") == "snapshot"
    assert _normalize_isolation("snapshot_isolation") == "snapshot"
    assert _normalize_isolation("snapshot") == "snapshot"
    client = make_client()
    await client._open()
    try:
        tx = client.transaction(isolation="serializable")
        assert tx._isolation == "snapshot"  # alias mapped, not propagated verbatim
    finally:
        await client.close()


def test_transaction_docs_do_not_claim_serializable_isolation() -> None:
    # No connector doc may present serializable isolation as a guarantee. Every
    # mention of "serializable" must sit in a sentence that scopes it as a negation
    # or a deprecated alias. Checked at sentence level so Markdown line wrapping does
    # not split the negation away from the word.
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    docs = [
        repo_root / "README.md",
        repo_root / "docs" / "TRANSACTIONS.md",
        repo_root / "docs" / "AURADB.md",
        repo_root / "docs" / "CLIENT.md",
        repo_root / "docs" / "COMPATIBILITY.md",
    ]
    transactions = (repo_root / "docs" / "TRANSACTIONS.md").read_text()
    assert "snapshot isolation" in transactions.lower()

    scoping_terms = ("deprecat", "alias", "not serializable", "not upgrade", "does not")
    for doc in docs:
        # Collapse whitespace/newlines, then split into sentences so a wrapped
        # negation ("... does not upgrade\nAuraDB transactions to serializable ...")
        # is evaluated as one unit.
        normalized = " ".join(doc.read_text().split())
        for sentence in normalized.replace("`", "").split(". "):
            low = sentence.lower()
            if "serializable" not in low:
                continue
            assert any(term in low for term in scoping_terms), (
                f"{doc.name} mentions 'serializable' without scoping it as a negation "
                f"or deprecated alias: {sentence.strip()!r}"
            )
