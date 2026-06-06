"""Live cluster conformance for the AuraDB multi-node preview.

These tests are skipped unless a preview cluster is configured through the
environment. They drive the connector against a real leader and follower to
verify the cluster ergonomics end to end:

* ``AURADB_CLUSTER_LEADER_DSN``   — e.g. ``auradbs://leader:7171/itest``
* ``AURADB_CLUSTER_FOLLOWER_DSN`` — e.g. ``auradbs://follower:7171/itest``
* ``AURADB_CLUSTER_TOKEN``        — static auth token, if auth is enabled
* ``AURADB_CLUSTER_CA``           — CA bundle path, for ``auradbs://`` TLS

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
