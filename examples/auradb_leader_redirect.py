"""Leader discovery and safe redirect with Aura Connector.

AuraDB's multi-node mode is an **experimental, opt-in preview** — there is no
production high availability or automatic failover, and single-node mode remains
the recommended production deployment. This example focuses on the three safe
ways to reach the leader, in order of preference:

1. **Discover the leader out of band** and connect straight to it. An operator
   resolves the leader with the CLI::

       auradb cluster leader --addr <any-node> --json

   and exports it (or any leader-hint source) as ``AURADB_CLUSTER_LEADER_DSN``.
   Pre-resolving the leader avoids a redirect round-trip entirely.

2. **Catch ``AuraNotLeaderError`` and reconnect explicitly.** The error renders a
   readable, actionable message (leader address + redirect guidance) and exposes
   the routing hints as attributes. ``Client.connect_to_leader(exc)`` opens a new
   client bound to the leader, preserving token auth and TLS unchanged. A
   redirect that would silently drop TLS is refused unless ``allow_insecure=True``.

3. **Opt in to a bounded redirect helper** for autonomous writes. It redirects
   only on ``not_leader`` (a pre-application rejection, so a write cannot be
   double-applied) and never more than ``max_redirects`` times.

Transactions and streaming cursors are **never** redirected: their server-side
state lives on one node. On ``not_leader`` inside a transaction, restart the whole
transaction on the leader — never migrate it.

Configure a real cluster with::

    export AURADB_CLUSTER_LEADER_DSN=auradbs://leader.example:7171/app
    export AURADB_CLUSTER_FOLLOWER_DSN=auradbs://follower.example:7171/app
    export AURADB_CLUSTER_TOKEN=...            # static auth token, if enabled
    export AURADB_CLUSTER_CA=/path/to/ca.pem   # CA bundle for auradbs:// TLS

With no cluster configured, it runs against the in-process reference server.

Run: ``python examples/auradb_leader_redirect.py``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, AuraNotLeaderError, Field, Model
from aura.config import TLSConfig, TokenAuth
from aura.errors import AuraTransactionError


class Record(Model):
    id: int = Field(primary_key=True)
    value: str


def _connect_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"models": [Record]}
    token = os.environ.get("AURADB_CLUSTER_TOKEN")
    if token:
        kwargs["auth"] = TokenAuth(token)
    ca = os.environ.get("AURADB_CLUSTER_CA")
    if ca:
        # verify_hostname stays on; the redirect never silently weakens it.
        kwargs["tls"] = TLSConfig(enabled=True, ca_cert_path=ca, verify_hostname=True)
    return kwargs


async def explicit_reconnect(follower_dsn: str) -> None:
    """Pattern 2: catch not_leader, print the guidance, reconnect to the leader."""
    async with Aura.connect(follower_dsn, **_connect_kwargs()) as client:
        try:
            await client.insert(Record(id=1, value="hello"))
            print("this node accepted the write (it is the leader)")
            return
        except AuraNotLeaderError as exc:
            # The string form is self-documenting: node reached, leader address,
            # retry classification, and how to redirect — no secrets included.
            print("not_leader:", exc)
            if exc.leader_addr is None:
                print("  no usable leader yet; run `auradb cluster leader` and retry")
                return
            leader = await client.connect_to_leader(exc)  # token auth + TLS preserved
            try:
                await leader.insert(Record(id=1, value="hello"))
                print("  re-sent to the leader at", exc.leader_addr)
            finally:
                await leader.close()


async def bounded_redirect(dsn: str) -> None:
    """Pattern 3: opt in to a bounded, one-hop redirect for an autonomous write."""
    async with Aura.connect(dsn, **_connect_kwargs()) as client:
        leader = client.with_leader_redirect(max_redirects=1)
        await leader.upsert(Record, key={"id": 2}, values={"value": "via redirect helper"})
        print("write landed on the leader via with_leader_redirect")
        # Transactions are intentionally not redirectable through the helper:
        try:
            leader.transaction()
        except AuraTransactionError as exc:
            print("transaction redirect refused (expected):", type(exc).__name__)


async def main() -> None:
    leader_dsn = os.environ.get("AURADB_CLUSTER_LEADER_DSN")
    follower_dsn = os.environ.get("AURADB_CLUSTER_FOLLOWER_DSN")

    if not leader_dsn and not follower_dsn:
        print("No cluster DSNs set; using the in-memory reference server (single node).")
        await bounded_redirect("aura+memory://localhost/app")
        return

    # Pattern 1: prefer the pre-resolved leader when an operator provided it.
    if leader_dsn:
        await bounded_redirect(leader_dsn)
    # Patterns 2/3 against a follower, when one is configured.
    if follower_dsn:
        await explicit_reconnect(follower_dsn)


if __name__ == "__main__":
    asyncio.run(main())
