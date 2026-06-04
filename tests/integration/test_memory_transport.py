"""Integration tests for the in-memory reference server through the protocol."""

from __future__ import annotations

from aura.protocol import Frame, Opcode, decode_body, encode_body
from aura.protocol.messages import (
    MutationRequest,
    PingRequest,
    QueryRequest,
    SchemaResponse,
)
from aura.transport.memory import MemoryTransport, ReferenceServer


async def roundtrip(transport: MemoryTransport, frame: Frame) -> Frame:
    return await transport.request(frame)


async def test_ping_pong() -> None:
    async with MemoryTransport() as t:
        frame = Frame(
            opcode=Opcode.PING,
            payload=encode_body(PingRequest(nonce=11).to_payload()),
            request_id=11,
        )
        response = await roundtrip(t, frame)
        assert response.opcode is Opcode.PONG
        assert decode_body(response.payload)["nonce"] == 11


async def test_schema_register_and_fetch() -> None:
    server = ReferenceServer()
    async with MemoryTransport(server) as t:
        models = [{"name": "User", "primary_key": "id", "fields": [], "relationships": []}]
        reg = Frame(opcode=Opcode.SCHEMA, payload=encode_body({"models": models}), request_id=1)
        response = await roundtrip(t, reg)
        body = SchemaResponse.from_payload(decode_body(response.payload))
        assert body.models[0]["name"] == "User"


async def test_insert_and_query() -> None:
    server = ReferenceServer()
    server.load_schema([{"name": "User", "primary_key": "id", "fields": [], "relationships": []}])
    async with MemoryTransport(server) as t:
        insert = Frame(
            opcode=Opcode.MUTATION,
            payload=encode_body(
                MutationRequest(
                    ir={"operation": "insert", "model": "User", "rows": [{"id": 1, "name": "A"}]}
                ).to_payload()
            ),
            request_id=1,
        )
        ins_resp = await roundtrip(t, insert)
        assert ins_resp.opcode is Opcode.MUTATION_RESULT
        assert decode_body(ins_resp.payload)["affected"] == 1

        query = Frame(
            opcode=Opcode.QUERY,
            payload=encode_body(
                QueryRequest(ir={"operation": "select", "model": "User"}).to_payload()
            ),
            request_id=2,
        )
        q_resp = await roundtrip(t, query)
        assert q_resp.opcode is Opcode.QUERY_RESULT
        rows = decode_body(q_resp.payload)["rows"]
        assert rows[0]["name"] == "A"


async def test_error_frame_on_bad_opcode_payload() -> None:
    async with MemoryTransport() as t:
        bad = Frame(
            opcode=Opcode.QUERY,
            payload=encode_body(QueryRequest(ir={"operation": "nonsense"}).to_payload()),
            request_id=9,
        )
        response = await roundtrip(t, bad)
        assert response.opcode is Opcode.ERROR


async def test_request_correlation_preserved() -> None:
    async with MemoryTransport() as t:
        frame = Frame(
            opcode=Opcode.PING, payload=encode_body(PingRequest().to_payload()), request_id=777
        )
        response = await roundtrip(t, frame)
        assert response.request_id == 777
