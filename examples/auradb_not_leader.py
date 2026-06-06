"""Handling AuraDB's ``not_leader`` response with Aura Connector.

In the experimental multi-node preview (no production HA, no automatic failover),
only the Raft leader accepts writes. A write sent to a follower raises
:class:`aura.AuraNotLeaderError`, which exposes the leader-routing hints the
server provided so you can redirect without parsing the message:

    try:
        await client.insert(record)
    except AuraNotLeaderError as exc:
        # exc.leader_addr        -> best usable client address of the leader
        # exc.leader_client_addr -> the leader's declared client address
        # exc.leader_node_id     -> the recognized leader's node id
        # exc.current_node_id    -> the (non-leader) node that was reached
        # exc.retryable          -> True when a leader is known
        ...

This script connects to a follower (``AURADB_CLUSTER_FOLLOWER_DSN``) when one is
configured and shows two safe responses; otherwise it explains the contract and
exits cleanly so it runs anywhere.

Transactions are **not** auto-redirected: a transaction's server-side state lives
on one node. On ``not_leader`` inside a transaction, restart the whole transaction
on the leader (open a new client with ``connect_to_leader``), never migrate it.

Run: ``python examples/auradb_not_leader.py``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, AuraNotLeaderError, Field, Model
from aura.config import TLSConfig, TokenAuth


class Event(Model):
    id: int = Field(primary_key=True)
    kind: str


def _connect_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"models": [Event]}
    token = os.environ.get("AURADB_CLUSTER_TOKEN")
    if token:
        kwargs["auth"] = TokenAuth(token)
    ca = os.environ.get("AURADB_CLUSTER_CA")
    if ca:
        kwargs["tls"] = TLSConfig(enabled=True, ca_cert_path=ca)
    return kwargs


async def explicit_reconnect(follower_dsn: str) -> None:
    """Catch not_leader, then open a fresh client bound to the leader and retry."""
    async with Aura.connect(follower_dsn, **_connect_kwargs()) as client:
        try:
            await client.insert(Event(id=1, kind="created"))
            print("this node accepted the write (it is the leader)")
        except AuraNotLeaderError as exc:
            print("rejected by a follower:", exc.message)
            print("  current node:", exc.current_node_id)
            print("  leader node :", exc.leader_node_id)
            print("  leader addr :", exc.leader_addr)
            if not exc.retryable or exc.leader_addr is None:
                print("  no usable leader yet; resolve via `auradb cluster leader` and retry")
                return
            leader_client = await client.connect_to_leader(exc)
            try:
                # The redirect preserved auth and TLS; the new client is on the leader.
                await leader_client.insert(Event(id=1, kind="created"))
                print("  re-sent to the leader at", exc.leader_addr)
            finally:
                await leader_client.close()


async def bounded_redirect(follower_dsn: str) -> None:
    """Let the opt-in helper redirect the write to the leader, bounded to one hop."""
    async with Aura.connect(follower_dsn, **_connect_kwargs()) as client:
        leader = client.with_leader_redirect(max_redirects=1)
        await leader.insert(Event(id=2, kind="redirected"))
        print("write landed on the leader via with_leader_redirect")


async def main() -> None:
    follower_dsn = os.environ.get("AURADB_CLUSTER_FOLLOWER_DSN")
    if not follower_dsn:
        print(
            "No AURADB_CLUSTER_FOLLOWER_DSN set. In the multi-node preview, a write to a "
            "follower raises AuraNotLeaderError carrying the leader address; catch it and "
            "either reconnect with Client.connect_to_leader(exc) or use "
            "Client.with_leader_redirect(). Single-node mode never raises it."
        )
        # Show the happy path against the in-process reference server.
        async with Aura.connect("aura+memory://localhost/app", models=[Event]) as client:
            await client.insert(Event(id=1, kind="created"))
            print("reference server accepted the write (single node, no not_leader)")
        return

    await explicit_reconnect(follower_dsn)
    await bounded_redirect(follower_dsn)


if __name__ == "__main__":
    asyncio.run(main())
