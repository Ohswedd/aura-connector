"""TLS/auth-preservation, secure-redirect, and diagnostic-message tests.

These complement :mod:`tests.unit.test_leader_redirect` by pinning down the
ergonomics polish in Aura Connector 0.4.1:

* A3 — actionable error messages for the reconnect / redirect helpers;
* A4 — TLS and auth are preserved across a redirect, verification is never
  silently disabled, and a secure client refuses to be redirected to a plaintext
  address by default;
* A5 — transaction and streaming redirects are rejected with guidance.

Everything runs in-process with stub backends — no network, no live cluster.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

import pytest

from aura import AuraModel, AuraNotLeaderError, Field
from aura.backends.base import Backend, BackendResult
from aura.backends.capabilities import BackendCapabilities
from aura.client import Client
from aura.config import TLSConfig, TokenAuth, parse_dsn
from aura.errors import (
    AuraBackendCapabilityError,
    AuraConnectionError,
    AuraQueryError,
    AuraTransactionError,
)
from aura.transport.memory import MemoryTransport, ReferenceServer


class Widget(AuraModel):
    id: int = Field(primary_key=True)
    name: str


_CAPS = BackendCapabilities(name="auradb", transactions=True, schema_create=True)


class _StubBackend(Backend):
    name = "auradb"

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.mutations = 0

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def ping(self) -> bool:
        return self.connected

    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        return BackendResult(rows=[], count=0)

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        self.mutations += 1
        return BackendResult(affected=1)

    def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        async def _empty() -> AsyncIterator[dict[str, Any]]:
            if False:  # pragma: no cover - never yields
                yield {}

        return _empty()

    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        return None

    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        return None

    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> Any:
        raise AuraBackendCapabilityError("no live migrations")

    def capabilities(self) -> BackendCapabilities:
        return _CAPS


class _FollowerBackend(_StubBackend):
    """Rejects writes with not_leader pointing at a fixed leader address."""

    def __init__(self, leader_addr: str | None = "leader:7171") -> None:
        super().__init__()
        self.leader_addr = leader_addr

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        raise AuraNotLeaderError(
            "this node is not the leader",
            leader_addr=self.leader_addr,
            current_node_id="0000000000000001",
        )


def _client(
    backend: Backend, *, dsn: str = "auradb://node-a:7171/test", **overrides: Any
) -> Client:
    """A client over a fixed stub backend (for error-path and follower tests)."""
    config = parse_dsn(dsn).with_overrides(**overrides)
    return Client(config, backend=backend)


def _mem_client(**overrides: Any) -> Client:
    """A client over the in-memory reference transport.

    ``reconnect_to`` opens a *real* sibling backend, so these tests use the
    in-process ``aura+memory`` transport — the redirect machinery still applies
    auth/TLS/scheme to the new config, which is what we assert, without any socket.
    """
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(**overrides)
    return Client(config, MemoryTransport(ReferenceServer()), [Widget])


# --------------------------------------------------------------------------- #
# A3 — diagnostic messages                                                    #
# --------------------------------------------------------------------------- #
async def test_connect_to_leader_missing_address_error_message() -> None:
    client = _client(_StubBackend())
    await client._open()
    err = AuraNotLeaderError("no leader yet")  # carries no leader_addr
    with pytest.raises(AuraConnectionError) as info:
        await client.connect_to_leader(err)
    msg = str(info.value)
    assert "leader" in msg
    assert "reconnect" in msg or "auradb cluster leader" in msg
    await client.close()


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        ("", "required"),
        ("no-colon", "host:port"),
        ("host:not-a-port", "non-numeric port"),
    ],
)
async def test_reconnect_to_invalid_address_error_message(bad: str, needle: str) -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraConnectionError) as info:
        await client.reconnect_to(bad)
    assert needle in str(info.value)
    await client.close()


async def test_redirect_max_redirects_error_message() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraQueryError) as info:
        client.with_leader_redirect(max_redirects=99)
    assert "bounded" in str(info.value)
    await client.close()


async def test_redirect_active_transaction_error_message() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraTransactionError) as info:
        client.with_leader_redirect().transaction()
    assert "restart the transaction on" in str(info.value)
    await client.close()


async def test_redirect_streaming_cursor_error_message() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraBackendCapabilityError) as info:
        client.with_leader_redirect().stream()
    assert "new query" in str(info.value)
    await client.close()


# --------------------------------------------------------------------------- #
# A4 — TLS / auth edge cases                                                   #
# --------------------------------------------------------------------------- #
async def test_redirect_preserves_token_auth() -> None:
    auth = TokenAuth("secret-token")
    client = _mem_client(auth=auth)
    await client._open()
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader.config.auth is auth
        assert leader.config.host == "node-b" and leader.config.port == 7272
    finally:
        await leader.close()
        await client.close()


async def test_redirect_preserves_tls_ca() -> None:
    tls = TLSConfig(enabled=True, ca_cert_path="/etc/aura/ca.pem", verify_hostname=True)
    client = _mem_client(tls=tls)
    await client._open()
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader.config.tls.ca_cert_path == "/etc/aura/ca.pem"
        assert leader.config.tls.enabled is True
    finally:
        await leader.close()
        await client.close()


async def test_redirect_preserves_server_name() -> None:
    # The connector derives the TLS server name from the connection host, so a
    # redirect recalculates it safely to the leader host while keeping hostname
    # verification on — it is never pinned to the old follower host.
    tls = TLSConfig(enabled=True, ca_cert_path="/etc/aura/ca.pem", verify_hostname=True)
    client = _mem_client(tls=tls)
    await client._open()
    leader = await client.reconnect_to("leader:7272")
    try:
        assert leader.config.host == "leader"  # server name recalculated to the leader
        assert leader.config.tls.verify_hostname is True
    finally:
        await leader.close()
        await client.close()


async def test_redirect_does_not_disable_tls_verification() -> None:
    tls = TLSConfig(enabled=True, verify_hostname=True)
    client = _mem_client(tls=tls)
    await client._open()
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader.config.tls.enabled is True
        assert leader.config.tls.verify_hostname is True  # never silently weakened
    finally:
        await leader.close()
        await client.close()


async def test_redirect_secure_to_insecure_rejected_by_default() -> None:
    # A TLS-enabled client (regardless of transport) must refuse an explicit
    # plaintext redirect target that would silently drop TLS.
    client = _mem_client(tls=TLSConfig(enabled=True))
    await client._open()
    with pytest.raises(AuraConnectionError) as info:
        await client.reconnect_to("auradb://leader:7272")
    assert "insecure" in str(info.value)
    # The escape hatch is explicit and opt-in.
    leader = await client.reconnect_to("auradb://leader:7272", allow_insecure=True)
    try:
        assert leader.config.host == "leader"
    finally:
        await leader.close()
        await client.close()


async def test_redirect_token_not_in_error_string() -> None:
    follower = _FollowerBackend(leader_addr=None)  # not_leader without an address
    client = _client(follower, dsn="auradb://node-a:7171/test", auth=TokenAuth("hunter2-token"))
    await client._open()
    with pytest.raises(AuraNotLeaderError) as info:
        await client.insert(Widget(id=1, name="a"))
    rendered = str(info.value) + repr(info.value)
    assert "hunter2-token" not in rendered
    await client.close()


async def test_redirect_host_port_address() -> None:
    client = _mem_client()
    await client._open()
    leader = await client.reconnect_to("198.51.100.7:7272")
    try:
        assert leader.config.host == "198.51.100.7"
        assert leader.config.port == 7272
    finally:
        await leader.close()
        await client.close()


async def test_redirect_unknown_scheme_rejected() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraConnectionError) as info:
        await client.reconnect_to("ftp://leader:7272")
    assert "unsupported scheme" in str(info.value)
    await client.close()


# --------------------------------------------------------------------------- #
# A5 — transaction / stream redirect safety                                   #
# --------------------------------------------------------------------------- #
async def test_transaction_not_redirected_after_not_leader() -> None:
    follower = _FollowerBackend()
    client = _client(follower)
    await client._open()
    with pytest.raises(AuraNotLeaderError):
        async with client.transaction() as txn:
            await txn.insert(Widget(id=1, name="a"))
    # The backend was not swapped behind the caller's back.
    assert client.backend is follower
    await client.close()


async def test_transaction_error_mentions_restart_on_leader() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraTransactionError) as info:
        client.with_leader_redirect().transaction()
    text = str(info.value)
    assert "restart the transaction on" in text
    assert "connect_to_leader" in text
    await client.close()


async def test_streaming_redirect_error_mentions_new_query() -> None:
    client = _client(_StubBackend())
    await client._open()
    with pytest.raises(AuraBackendCapabilityError) as info:
        client.with_leader_redirect().stream()
    assert "new query" in str(info.value)
    await client.close()


async def test_redirect_helper_docs_examples_transaction_safe() -> None:
    # The documented contract: the redirect helper never exposes a transaction
    # entry point that could silently migrate server-side state across nodes.
    client = _client(_StubBackend())
    await client._open()
    redirect = client.with_leader_redirect()
    with pytest.raises(AuraTransactionError):
        redirect.transaction()
    with pytest.raises(AuraBackendCapabilityError):
        redirect.stream()
    await client.close()
