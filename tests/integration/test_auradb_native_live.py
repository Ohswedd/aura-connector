"""Live integration test for the native AuraDB backend.

This test is skipped unless ``AURADB_TEST_ADDR`` points at a running AuraDB
server (for example ``127.0.0.1:7171``). It exercises the public client API end
to end against the real server. Optionally set ``AURADB_TEST_TOKEN`` for an
auth-enabled server and ``AURADB_TEST_TLS_CA`` for a TLS server.
"""

from __future__ import annotations

import os

import pytest

from aura import AuraModel, Field, Vector, connect
from aura.config import TLSConfig, TokenAuth

ADDR = os.environ.get("AURADB_TEST_ADDR")
pytestmark = pytest.mark.skipif(not ADDR, reason="set AURADB_TEST_ADDR to run live AuraDB tests")


class Note(AuraModel):
    id: str = Field(primary_key=True)
    topic: str = Field(index=True)
    body: str
    embedding: Vector[3]


def _options() -> dict:
    options: dict = {}
    token = os.environ.get("AURADB_TEST_TOKEN")
    ca = os.environ.get("AURADB_TEST_TLS_CA")
    if token:
        options["auth"] = TokenAuth(token)
    if ca:
        options["tls"] = TLSConfig(enabled=True, ca_cert_path=ca, verify_hostname=True)
    return options


async def test_native_backend_round_trip():
    scheme = "auradbs" if os.environ.get("AURADB_TEST_TLS_CA") else "auradb"
    dsn = f"{scheme}://{ADDR}/itest"
    async with connect(dsn, models=[Note], **_options()) as client:
        assert await client.ping()
        await client.insert(
            Note(id="n1", topic="rust", body="alpha beta", embedding=[1.0, 0.0, 0.0])
        )
        await client.insert(
            Note(id="n2", topic="python", body="beta gamma", embedding=[0.0, 1.0, 0.0])
        )

        assert await client.query(Note).count() == 2
        rust = await client.query(Note).where(Note.topic == "rust").all()
        assert [n.id for n in rust] == ["n1"]

        text = await client.query(Note).text(Note.body, query="beta").all()
        assert {n.id for n in text} == {"n1", "n2"}

        near = (
            await client.search(Note)
            .nearest(Note.embedding, [1.0, 0.0, 0.0], metric="cosine", limit=1)
            .all()
        )
        assert near[0].id == "n1"

        assert await client.delete(Note).where(Note.id == "n2").execute() == 1
        assert await client.query(Note).count() == 1

        # Transaction-scoped reads (AuraDB v0.2.0): a read issued inside the
        # transaction sees the transaction's own staged write, while a
        # non-transactional read does not until commit.
        async with client.transaction() as tx:
            await tx.insert(
                Note(id="n3", topic="zig", body="delta epsilon", embedding=[0.0, 0.0, 1.0])
            )
            assert await tx.query(Note).where(Note.id == "n3").count() == 1
            assert await client.query(Note).where(Note.id == "n3").count() == 0
        assert await client.query(Note).where(Note.id == "n3").count() == 1
