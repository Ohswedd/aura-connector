"""Integration test for the TCP transport against a loopback reference server.

Spins up a real ``asyncio`` TCP server that frames requests through the reference
server, then drives it with :class:`TCPTransport` to prove the transport works over a
genuine socket.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from aura import AuraModel, Field
from aura.client import Client
from aura.config import parse_dsn
from aura.protocol.codec import try_decode_frame
from aura.transport.memory import ReferenceServer
from aura.transport.tcp import TCPTransport


class Note(AuraModel):
    id: int = Field(primary_key=True)
    text: str


async def _serve(
    server: ReferenceServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    from aura.protocol.codec import encode_frame

    buffer = bytearray()
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                frame, consumed = try_decode_frame(bytes(buffer))
                if frame is None:
                    break
                del buffer[:consumed]
                response = server.handle(frame)
                writer.write(encode_frame(response, payload_checksum=True))
                await writer.drain()
    except (asyncio.CancelledError, ConnectionError):
        pass
    finally:
        writer.close()
        with suppress(asyncio.CancelledError, ConnectionError, OSError):
            await writer.wait_closed()


@pytest.fixture
async def tcp_server():
    server = ReferenceServer()
    conn_tasks: set[asyncio.Task[None]] = set()

    def _on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.ensure_future(_serve(server, reader, writer))
        conn_tasks.add(task)
        task.add_done_callback(conn_tasks.discard)

    listener = await asyncio.start_server(_on_connect, host="127.0.0.1", port=0)
    port = listener.sockets[0].getsockname()[1]
    try:
        await listener.start_serving()
        yield server, port
    finally:
        # Deterministically tear down connection handlers so no StreamWriter is left to
        # be finalized by the GC (which would surface as an unraisable warning).
        for task in list(conn_tasks):
            task.cancel()
        for task in list(conn_tasks):
            with suppress(asyncio.CancelledError):
                await task
        listener.close()
        await listener.wait_closed()


async def test_tcp_ping_and_crud(tcp_server) -> None:
    _server, port = tcp_server
    config = parse_dsn(f"aura://127.0.0.1:{port}/test")
    transport = TCPTransport(config)
    client = Client(config, transport, [Note])
    await client._open()
    try:
        assert await client.ping() is True
        await client.insert(Note(id=1, text="hello"))
        got = await client.Note.find(id=1)
        assert got.text == "hello"
        assert await client.query(Note).count() == 1
    finally:
        await client.close()
        assert transport.is_connected is False


async def test_tcp_concurrent_requests(tcp_server) -> None:
    _server, port = tcp_server
    config = parse_dsn(f"aura://127.0.0.1:{port}/test")
    transport = TCPTransport(config)
    client = Client(config, transport, [Note])
    await client._open()
    try:
        await client.bulk_insert(Note, [Note(id=i, text=f"n{i}") for i in range(20)])
        results = await asyncio.gather(*(client.Note.find(id=i) for i in range(20)))
        assert sorted(n.id for n in results) == list(range(20))
    finally:
        await client.close()
