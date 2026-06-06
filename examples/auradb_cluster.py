"""Cluster-aware AuraDB usage with Aura Connector.

AuraDB's multi-node mode is an **experimental, opt-in preview** — there is no
production high availability or automatic failover, and single-node mode remains
the recommended production deployment. In the preview, only the Raft leader
accepts writes; a write sent to a follower is rejected with a structured
``not_leader`` response. This example shows the connector ergonomics for that:

* the dedicated :class:`aura.AuraNotLeaderError` and its leader-routing fields;
* :meth:`Client.connect_to_leader` — open a new client bound to the leader,
  preserving the original token auth and TLS settings;
* :meth:`Client.with_leader_redirect` — an opt-in, *bounded* helper that retries
  a write on the leader, safe because ``not_leader`` is a pre-application
  rejection (the follower refuses the write before it enters the Raft log).

By default this runs against the in-process reference server so it executes
anywhere. Point it at a real preview cluster by exporting::

    export AURADB_CLUSTER_LEADER_DSN=auradbs://leader.example:7171/app
    export AURADB_CLUSTER_FOLLOWER_DSN=auradbs://follower.example:7171/app
    export AURADB_CLUSTER_TOKEN=...            # static auth token, if enabled
    export AURADB_CLUSTER_CA=/path/to/ca.pem   # CA bundle for auradbs:// TLS

Run: ``python examples/auradb_cluster.py``
"""

from __future__ import annotations

import asyncio
import os

from aura import Aura, AuraNotLeaderError, Field, Model
from aura.config import TLSConfig, TokenAuth


class Note(Model):
    id: int = Field(primary_key=True)
    body: str


def _connect_kwargs() -> dict[str, object]:
    """Build auth/TLS kwargs from the environment, when a real cluster is configured."""
    kwargs: dict[str, object] = {"models": [Note]}
    token = os.environ.get("AURADB_CLUSTER_TOKEN")
    if token:
        kwargs["auth"] = TokenAuth(token)
    ca = os.environ.get("AURADB_CLUSTER_CA")
    if ca:
        kwargs["tls"] = TLSConfig(enabled=True, ca_cert_path=ca)
    return kwargs


async def against_leader(dsn: str) -> None:
    """Connect to the known leader and use the bounded redirect helper for writes."""
    async with Aura.connect(dsn, **_connect_kwargs()) as client:
        # Opt in to bounded leader redirection: at most one redirect, and only on a
        # not_leader response that carries a usable leader address. Plain client
        # calls (outside the helper) never redirect.
        leader = client.with_leader_redirect(max_redirects=1)
        await leader.insert(Note(id=1, body="hello from a cluster-aware client"))
        await leader.upsert(Note, key={"id": 1}, values={"body": "updated via leader"})

        note = await client.Note.find(id=1)
        print("stored:", note.body)


async def reconnect_pattern(follower_dsn: str) -> None:
    """Catch not_leader on a follower and open a new client bound to the leader."""
    async with Aura.connect(follower_dsn, **_connect_kwargs()) as client:
        try:
            await client.insert(Note(id=2, body="written to whoever answers"))
        except AuraNotLeaderError as exc:
            print("not leader:", exc)
            if exc.leader_addr is None:
                # No usable leader address yet — resolve it out of band, e.g. with
                # `auradb cluster leader --addr <node>`, then reconnect explicitly.
                print("leader address unknown; resolve it and retry")
                return
            # A new client bound to the leader, inheriting auth + TLS unchanged.
            leader_client = await client.connect_to_leader(exc)
            try:
                await leader_client.insert(Note(id=2, body="re-sent to the leader"))
                print("write succeeded after redirect to", exc.leader_addr)
            finally:
                await leader_client.close()


async def main() -> None:
    leader_dsn = os.environ.get("AURADB_CLUSTER_LEADER_DSN")
    follower_dsn = os.environ.get("AURADB_CLUSTER_FOLLOWER_DSN")

    if not leader_dsn:
        # No cluster configured: exercise the same API surface against the
        # in-process reference server so the example runs everywhere.
        print("No AURADB_CLUSTER_LEADER_DSN set; using the in-memory reference server.")
        await against_leader("aura+memory://localhost/app")
        return

    await against_leader(leader_dsn)
    if follower_dsn:
        await reconnect_pattern(follower_dsn)


if __name__ == "__main__":
    asyncio.run(main())
