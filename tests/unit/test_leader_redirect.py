"""Unit tests for the cluster leader reconnect and bounded-redirect helpers.

These cover the AuraDB multi-node preview ergonomics on the client side:

* :meth:`Client.connect_to_leader` / :meth:`Client.reconnect_to` (A3)
* :meth:`Client.with_leader_redirect` and :class:`LeaderRedirect` (A4)
* transaction- and stream-safety rules (A5)

The redirect path is exercised with in-process stub backends so it runs anywhere,
with no network and no live cluster.
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


# --------------------------------------------------------------------------- #
# Stub backends                                                               #
# --------------------------------------------------------------------------- #
_CAPS = BackendCapabilities(name="auradb", transactions=True, schema_create=True)


class _StubBackend(Backend):
    """Minimal in-process backend; subclasses override mutation behavior."""

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
    """Rejects writes with not_leader, pointing at a fixed leader address."""

    def __init__(self, leader_addr: str | None = "leader:7171") -> None:
        super().__init__()
        self.leader_addr = leader_addr

    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        raise AuraNotLeaderError(
            "this node is not the leader",
            leader_addr=self.leader_addr,
            current_node_id="0000000000000001",
        )


def _auradb_client(backend: Backend, *, scheme: str = "auradb") -> Client:
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(scheme=scheme)
    return Client(config, backend=backend)


# --------------------------------------------------------------------------- #
# A3 — reconnect / connect_to_leader                                          #
# --------------------------------------------------------------------------- #
async def test_reconnect_to_leader_requires_address() -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    err = AuraNotLeaderError("no leader yet")  # no leader_addr
    with pytest.raises(AuraConnectionError):
        await client.connect_to_leader(err)


async def test_reconnect_to_leader_preserves_token_auth() -> None:
    auth = TokenAuth("secret-token")
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(auth=auth)
    client = Client(config, MemoryTransport(ReferenceServer()), [Widget])
    await client._open()
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader.config.auth is auth  # same credential object, not dropped
        assert leader.config.host == "node-b"
        assert leader.config.port == 7272
        assert leader.config.scheme == config.scheme
    finally:
        await leader.close()
        await client.close()


async def test_reconnect_to_leader_preserves_tls_config() -> None:
    tls = TLSConfig(enabled=True, ca_cert_path="/tmp/ca.pem", verify_hostname=True)
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(tls=tls)
    client = Client(config, MemoryTransport(ReferenceServer()), [Widget])
    await client._open()
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader.config.tls == tls
        assert leader.config.tls.verify_hostname is True  # verification not silently weakened
    finally:
        await leader.close()
        await client.close()


async def test_reconnect_to_leader_does_not_preserve_transaction() -> None:
    config = parse_dsn("aura+memory://node-a:7171/test")
    client = Client(config, MemoryTransport(ReferenceServer()), [Widget])
    await client._open()
    client.transaction()  # bump the transaction counter on the original client
    assert client._tx_counter == 1
    leader = await client.reconnect_to("node-b:7272")
    try:
        assert leader is not client
        assert leader._tx_counter == 0  # fresh client carries no transaction state
    finally:
        await leader.close()
        await client.close()


@pytest.mark.parametrize("bad", ["", "   ", "no-colon", "host:not-a-port"])
async def test_reconnect_to_leader_rejects_invalid_address(bad: str) -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    with pytest.raises(AuraConnectionError):
        await client.reconnect_to(bad)


async def test_reconnect_to_leader_noop_for_non_auradb_rejected() -> None:
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(scheme="sqlite")
    client = Client(config, backend=_StubBackend())
    await client._open()
    with pytest.raises(AuraBackendCapabilityError):
        await client.reconnect_to("leader:7171")


# --------------------------------------------------------------------------- #
# A4 — bounded leader redirect                                                #
# --------------------------------------------------------------------------- #
async def test_leader_redirect_disabled_by_default() -> None:
    follower = _FollowerBackend()
    client = _auradb_client(follower)
    await client._open()
    # A plain client call never redirects: the not_leader error propagates.
    with pytest.raises(AuraNotLeaderError):
        await client.insert(Widget(id=1, name="a"))


async def test_leader_redirect_bounded() -> None:
    follower = _FollowerBackend()
    leader = _StubBackend()
    client = _auradb_client(follower)
    await client._open()
    client._build_backend = lambda config: leader  # type: ignore[assignment]
    redirect = client.with_leader_redirect(max_redirects=1)
    result = await redirect.insert(Widget(id=1, name="a"))
    assert result is not None
    assert leader.connected is True  # redirected to the leader
    assert leader.mutations == 1  # write applied exactly once on the leader
    assert follower.closed is True  # old connection released


async def test_leader_redirect_preserves_auth_tls() -> None:
    auth = TokenAuth("secret-token")
    tls = TLSConfig(enabled=True, ca_cert_path="/tmp/ca.pem")
    config = parse_dsn("aura+memory://node-a:7171/test").with_overrides(
        scheme="auradb", auth=auth, tls=tls
    )
    follower = _FollowerBackend()
    leader = _StubBackend()
    client = Client(config, backend=follower)
    await client._open()
    captured: dict[str, Any] = {}

    def build(cfg: Any) -> Backend:
        captured["config"] = cfg
        return leader

    client._build_backend = build  # type: ignore[assignment]
    await client.with_leader_redirect(max_redirects=1).insert(Widget(id=1, name="a"))
    assert captured["config"].auth is auth  # auth carried across the redirect
    assert captured["config"].tls == tls  # TLS carried across the redirect
    assert captured["config"].host == "leader"


async def test_leader_redirect_rejects_active_transaction() -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    redirect = client.with_leader_redirect()
    with pytest.raises(AuraTransactionError):
        redirect.transaction()


async def test_leader_redirect_does_not_redirect_streaming_cursor() -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    redirect = client.with_leader_redirect()
    with pytest.raises(AuraBackendCapabilityError):
        redirect.stream()


async def test_leader_redirect_requires_leader_addr() -> None:
    follower = _FollowerBackend(leader_addr=None)  # not_leader without an address
    client = _auradb_client(follower)
    await client._open()
    redirect = client.with_leader_redirect(max_redirects=3)
    # No usable leader address => cannot redirect; the error propagates unchanged.
    with pytest.raises(AuraNotLeaderError):
        await redirect.insert(Widget(id=1, name="a"))


async def test_leader_redirect_no_infinite_loop() -> None:
    # Every node says "not leader" pointing elsewhere; redirects must stay bounded.
    follower = _FollowerBackend(leader_addr="other:7171")
    client = _auradb_client(follower)
    await client._open()
    rebuilds = {"n": 0}

    def build(cfg: Any) -> Backend:
        rebuilds["n"] += 1
        return _FollowerBackend(leader_addr="other:7171")  # still a follower

    client._build_backend = build  # type: ignore[assignment]
    with pytest.raises(AuraNotLeaderError):
        await client.with_leader_redirect(max_redirects=2).insert(Widget(id=1, name="a"))
    assert rebuilds["n"] == 2  # exactly max_redirects reconnects, then it gives up


@pytest.mark.parametrize("bad", [-1, 11, 1.5, True])
async def test_leader_redirect_validates_max_redirects(bad: Any) -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    with pytest.raises(AuraQueryError):  # out-of-range / wrong type
        client.with_leader_redirect(max_redirects=bad)


# --------------------------------------------------------------------------- #
# A5 — transaction-safe redirect rules                                        #
# --------------------------------------------------------------------------- #
async def test_not_leader_inside_transaction_no_auto_redirect() -> None:
    follower = _FollowerBackend()
    client = _auradb_client(follower)
    await client._open()
    # A write inside a transaction surfaces not_leader; it is never auto-redirected.
    with pytest.raises(AuraNotLeaderError):
        async with client.transaction() as txn:
            await txn.insert(Widget(id=1, name="a"))
    # The follower backend was not swapped out behind the user's back.
    assert client.backend is follower


async def test_transaction_context_preserved_on_error_cleanup() -> None:
    follower = _FollowerBackend()
    client = _auradb_client(follower)
    await client._open()
    txn = client.transaction()
    await txn.begin()
    with pytest.raises(AuraNotLeaderError):
        await txn.insert(Widget(id=1, name="a"))
    await txn.rollback()  # cleanup still works after a not_leader error
    assert txn._finished is True


async def test_streaming_cursor_not_redirected() -> None:
    client = _auradb_client(_StubBackend())
    await client._open()
    # The redirect helper refuses to wrap a stream rather than redirecting mid-page.
    with pytest.raises(AuraBackendCapabilityError):
        client.with_leader_redirect().stream()


async def test_read_operation_can_resolve_leader_before_execution_if_helper_enabled() -> None:
    # A read routed through the helper redirects to the leader just like a write,
    # because not_leader is reported before any rows are produced.
    class _FollowerReads(_FollowerBackend):
        async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
            raise AuraNotLeaderError("not leader", leader_addr="leader:7171")

    follower = _FollowerReads()
    leader = _StubBackend()
    client = _auradb_client(follower)
    await client._open()
    client.register_model(Widget)
    client._build_backend = lambda config: leader  # type: ignore[assignment]
    redirect = client.with_leader_redirect(max_redirects=1)
    rows = await redirect.run(lambda: client.query(Widget).all())
    assert rows == []  # resolved against the leader, no error surfaced
    assert leader.connected is True


# --------------------------------------------------------------------------- #
# A5 — the snapshot-isolation default must not weaken redirect safety         #
# --------------------------------------------------------------------------- #
async def test_transaction_no_auto_redirect_still_holds() -> None:
    # Regression guard for the snapshot-isolation default: a write inside a
    # transaction that hits not_leader still propagates unchanged and the backend is
    # never swapped behind the caller's back.
    follower = _FollowerBackend()
    client = _auradb_client(follower)
    await client._open()
    tx = client.transaction()
    assert tx._isolation == "snapshot"  # default normalized, never "serializable"
    with pytest.raises(AuraNotLeaderError):
        async with tx:
            await tx.insert(Widget(id=1, name="a"))
    assert client.backend is follower
    await client.close()


async def test_transaction_search_no_auto_redirect_still_holds() -> None:
    # A search/read inside a transaction runs against the same node; a subsequent
    # not_leader write is not auto-redirected, even when the deprecated
    # "serializable" alias was requested (it maps to snapshot isolation).
    follower = _FollowerBackend()
    client = _auradb_client(follower)
    await client._open()
    client.register_model(Widget)
    tx = client.transaction(isolation="serializable")
    assert tx._isolation == "snapshot"  # alias maps to snapshot, not propagated verbatim
    with pytest.raises(AuraNotLeaderError):
        async with tx:
            assert await tx.query(Widget).all() == []  # read served by the follower
            await tx.insert(Widget(id=1, name="a"))  # write surfaces not_leader
    assert client.backend is follower
    await client.close()
