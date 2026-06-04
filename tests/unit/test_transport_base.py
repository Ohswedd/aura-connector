"""Tests for the transport base interface and stats."""

from __future__ import annotations

import pytest

from aura.errors import AuraConnectionError
from aura.protocol import Frame, Opcode
from aura.protocol.messages import PingRequest, encode_body
from aura.transport.base import Transport, TransportStats
from aura.transport.memory import MemoryTransport


def test_stats_as_dict() -> None:
    stats = TransportStats()
    stats.frames_sent = 2
    stats.bytes_sent = 100
    assert stats.as_dict() == {
        "frames_sent": 2,
        "frames_received": 0,
        "bytes_sent": 100,
        "bytes_received": 0,
    }


def test_transport_is_abstract() -> None:
    with pytest.raises(TypeError):
        Transport()  # type: ignore[abstract]


async def test_memory_transport_requires_connection() -> None:
    transport = MemoryTransport()
    frame = Frame(opcode=Opcode.PING, payload=encode_body(PingRequest(nonce=1).to_payload()))
    with pytest.raises(AuraConnectionError):
        await transport.request(frame)


async def test_memory_transport_context_manager_tracks_stats() -> None:
    async with MemoryTransport() as transport:
        assert transport.is_connected is True
        frame = Frame(
            opcode=Opcode.PING,
            payload=encode_body(PingRequest(nonce=5).to_payload()),
            request_id=5,
        )
        response = await transport.request(frame)
        assert response.opcode is Opcode.PONG
        assert transport.stats.frames_sent == 1
        assert transport.stats.frames_received == 1
        assert transport.stats.bytes_sent > 0
    assert transport.is_connected is False


def test_describe() -> None:
    transport = MemoryTransport()
    info = transport.describe()
    assert info["type"] == "MemoryTransport"
    assert info["connected"] is False
