"""End-to-end protocol round-trip: every opcode the client uses, via the codec."""

from __future__ import annotations

import pytest

from aura.protocol import Frame, Opcode, decode_body, encode_body
from aura.protocol.messages import (
    HandshakeRequest,
    MutationRequest,
    QueryRequest,
    TxControl,
)
from aura.transport.memory import MemoryTransport, ReferenceServer


@pytest.fixture
def transport() -> MemoryTransport:
    server = ReferenceServer()
    server.load_schema(
        [{"name": "Account", "primary_key": "id", "fields": [], "relationships": []}]
    )
    return MemoryTransport(server)


async def test_handshake(transport: MemoryTransport) -> None:
    async with transport as t:
        frame = Frame(
            opcode=Opcode.HANDSHAKE,
            payload=encode_body(HandshakeRequest(client_version=1).to_payload()),
            request_id=1,
        )
        response = await t.request(frame)
        assert response.opcode is Opcode.HANDSHAKE_ACK


async def test_transaction_commit_visible(transport: MemoryTransport) -> None:
    async with transport as t:
        txid = 99
        await t.request(
            Frame(
                opcode=Opcode.BEGIN_TX,
                transaction_id=txid,
                payload=encode_body(TxControl("begin").to_payload()),
                request_id=1,
            )
        )
        await t.request(
            Frame(
                opcode=Opcode.MUTATION,
                transaction_id=txid,
                payload=encode_body(
                    MutationRequest(
                        ir={
                            "operation": "insert",
                            "model": "Account",
                            "rows": [{"id": 1, "balance": 100}],
                        }
                    ).to_payload()
                ),
                request_id=2,
            )
        )
        # Not visible outside the transaction yet.
        outside = await t.request(
            Frame(
                opcode=Opcode.QUERY,
                payload=encode_body(
                    QueryRequest(ir={"operation": "count", "model": "Account"}).to_payload()
                ),
                request_id=3,
            )
        )
        assert decode_body(outside.payload)["count"] == 0

        await t.request(
            Frame(
                opcode=Opcode.COMMIT_TX,
                transaction_id=txid,
                payload=encode_body(TxControl("commit").to_payload()),
                request_id=4,
            )
        )
        after = await t.request(
            Frame(
                opcode=Opcode.QUERY,
                payload=encode_body(
                    QueryRequest(ir={"operation": "count", "model": "Account"}).to_payload()
                ),
                request_id=5,
            )
        )
        assert decode_body(after.payload)["count"] == 1


async def test_transaction_rollback_discards(transport: MemoryTransport) -> None:
    async with transport as t:
        txid = 5
        await t.request(
            Frame(
                opcode=Opcode.BEGIN_TX,
                transaction_id=txid,
                payload=encode_body(TxControl("begin").to_payload()),
                request_id=1,
            )
        )
        await t.request(
            Frame(
                opcode=Opcode.MUTATION,
                transaction_id=txid,
                payload=encode_body(
                    MutationRequest(
                        ir={"operation": "insert", "model": "Account", "rows": [{"id": 1}]}
                    ).to_payload()
                ),
                request_id=2,
            )
        )
        await t.request(
            Frame(
                opcode=Opcode.ROLLBACK_TX,
                transaction_id=txid,
                payload=encode_body(TxControl("rollback").to_payload()),
                request_id=3,
            )
        )
        after = await t.request(
            Frame(
                opcode=Opcode.QUERY,
                payload=encode_body(
                    QueryRequest(ir={"operation": "count", "model": "Account"}).to_payload()
                ),
                request_id=4,
            )
        )
        assert decode_body(after.payload)["count"] == 0
