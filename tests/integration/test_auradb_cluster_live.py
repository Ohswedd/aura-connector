"""Live cluster conformance for the AuraDB multi-node preview.

These tests are skipped unless a preview cluster is configured through the
environment. They drive the connector against a real leader and follower to
verify the cluster ergonomics end to end:

* ``AURADB_CLUSTER_LEADER_DSN``   — e.g. ``auradbs://leader:7171/itest``
* ``AURADB_CLUSTER_FOLLOWER_DSN`` — e.g. ``auradbs://follower:7171/itest``
* ``AURADB_CLUSTER_TOKEN``        — static auth token, if auth is enabled
* ``AURADB_CLUSTER_CA``           — CA bundle path, for ``auradbs://`` TLS
* ``AURADB_CLUSTER_SERVER_NAME``  — expected TLS certificate name; the connector
  derives SNI / hostname verification from the DSN host, so set the DSN host to
  this name (there is no separate server-name knob). When set, it is asserted to
  match the leader/follower DSN host so a misconfiguration fails loudly.

The multi-node mode is experimental and opt-in; these are conformance checks for
the preview, not a claim of production high availability.
"""

from __future__ import annotations

import os

import pytest

from aura import AuraModel, AuraNotLeaderError, Field, connect
from aura.config import TLSConfig, TokenAuth

LEADER = os.environ.get("AURADB_CLUSTER_LEADER_DSN")
FOLLOWER = os.environ.get("AURADB_CLUSTER_FOLLOWER_DSN")

pytestmark = pytest.mark.skipif(
    not (LEADER and FOLLOWER),
    reason="set AURADB_CLUSTER_LEADER_DSN and AURADB_CLUSTER_FOLLOWER_DSN to run cluster tests",
)


class CItem(AuraModel):
    id: int = Field(primary_key=True)
    label: str


def _options() -> dict:
    options: dict = {}
    token = os.environ.get("AURADB_CLUSTER_TOKEN")
    ca = os.environ.get("AURADB_CLUSTER_CA")
    if token:
        options["auth"] = TokenAuth(token)
    if ca:
        options["tls"] = TLSConfig(enabled=True, ca_cert_path=ca, verify_hostname=True)
    return options


async def test_cluster_leader_smoke() -> None:
    async with connect(LEADER, models=[CItem], **_options()) as client:
        assert await client.ping()
        await client.upsert(CItem, key={"id": 1}, values={"label": "leader-write"})
        found = await client.CItem.find(id=1)
        assert found.label == "leader-write"


async def test_cluster_follower_not_leader_error() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        with pytest.raises(AuraNotLeaderError) as info:
            await client.insert(CItem(id=2, label="should-be-rejected"))
        # The error exposes a usable leader address (auth/TLS preserved on redirect).
        assert info.value.leader_addr, "follower not_leader error must carry a leader address"
        assert info.value.retryable is True


async def test_cluster_reconnect_to_leader() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        try:
            await client.insert(CItem(id=3, label="x"))
        except AuraNotLeaderError as exc:
            leader_client = await client.connect_to_leader(exc)
            try:
                await leader_client.upsert(CItem, key={"id": 3}, values={"label": "via-leader"})
                row = await leader_client.CItem.find(id=3)
                assert row.label == "via-leader"
            finally:
                await leader_client.close()
        else:  # pragma: no cover - the follower must reject the write
            pytest.fail("expected AuraNotLeaderError from the follower")


async def test_cluster_redirect_helper_bounded() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        leader = client.with_leader_redirect(max_redirects=1)
        # Bounded redirect resolves the leader and applies the write exactly once.
        await leader.upsert(CItem, key={"id": 4}, values={"label": "redirected"})
        # The client is now pointed at the leader; the same value is readable.
        row = await client.CItem.find(id=4)
        assert row.label == "redirected"


async def test_cluster_transaction_not_redirected() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        # A transaction is never auto-redirected: the not_leader surfaces to the caller.
        with pytest.raises(AuraNotLeaderError):
            async with client.transaction() as txn:
                await txn.insert(CItem(id=5, label="in-txn"))
        # The redirect helper refuses to wrap a transaction at all.
        from aura.errors import AuraTransactionError

        with pytest.raises(AuraTransactionError):
            client.with_leader_redirect().transaction()


# --------------------------------------------------------------------------- #
# A7 — explicitly-named conformance checks for the published connector         #
# --------------------------------------------------------------------------- #
def _dsn_host(dsn: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(dsn).hostname or ""


async def test_cluster_follower_not_leader_error_message() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        with pytest.raises(AuraNotLeaderError) as info:
            await client.insert(CItem(id=10, label="rejected"))
        text = str(info.value)
        # The rendered message carries the leader address and redirect guidance.
        assert info.value.leader_addr and info.value.leader_addr in text
        assert "connect_to_leader" in text
        # And it never leaks the auth token.
        token = os.environ.get("AURADB_CLUSTER_TOKEN")
        if token:
            assert token not in text and token not in repr(info.value)


async def test_cluster_connect_to_leader_live() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        try:
            await client.insert(CItem(id=11, label="x"))
            pytest.fail("expected AuraNotLeaderError from the follower")
        except AuraNotLeaderError as exc:
            leader_client = await client.connect_to_leader(exc)
            try:
                await leader_client.upsert(CItem, key={"id": 11}, values={"label": "via-leader"})
                assert (await leader_client.CItem.find(id=11)).label == "via-leader"
            finally:
                await leader_client.close()


async def test_cluster_redirect_helper_live() -> None:
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        leader = client.with_leader_redirect(max_redirects=1)
        await leader.upsert(CItem, key={"id": 12}, values={"label": "redirected"})
        assert (await client.CItem.find(id=12)).label == "redirected"


async def test_cluster_redirect_tls_auth_live() -> None:
    # After a redirect, the live client must still carry the configured TLS/auth.
    server_name = os.environ.get("AURADB_CLUSTER_SERVER_NAME")
    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        if server_name:
            assert _dsn_host(FOLLOWER) == server_name  # SNI is the DSN host
        try:
            await client.insert(CItem(id=13, label="x"))
            pytest.fail("expected AuraNotLeaderError from the follower")
        except AuraNotLeaderError as exc:
            leader_client = await client.connect_to_leader(exc)
            try:
                # Auth and TLS settings are inherited unchanged across the redirect.
                assert leader_client.config.auth is client.config.auth
                assert leader_client.config.tls.enabled == client.config.tls.enabled
                assert leader_client.config.tls.verify_hostname == client.config.tls.verify_hostname
                assert await leader_client.ping()
            finally:
                await leader_client.close()


async def test_cluster_transaction_redirect_rejected_live() -> None:
    from aura.errors import AuraTransactionError

    async with connect(FOLLOWER, models=[CItem], **_options()) as client:
        with pytest.raises(AuraNotLeaderError):
            async with client.transaction() as txn:
                await txn.insert(CItem(id=14, label="in-txn"))
        with pytest.raises(AuraTransactionError):
            client.with_leader_redirect().transaction()
