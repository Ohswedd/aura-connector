"""Abstract async transport interface.

A transport moves protocol frames between the client and a server, correlating each
request frame with its response. Concrete transports include an in-memory reference
server (:mod:`aura.transport.memory`) and a TCP transport
(:mod:`aura.transport.tcp`). The interface is intentionally small so a future QUIC
transport can satisfy it without changes elsewhere.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any

from ..protocol.frames import Frame

__all__ = ["Transport", "TransportStats"]


class TransportStats:
    """Lightweight, mutable transport counters for observability."""

    __slots__ = ("bytes_received", "bytes_sent", "frames_received", "frames_sent")

    def __init__(self) -> None:
        self.frames_sent = 0
        self.frames_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
        }


class Transport(ABC):
    """Abstract async transport."""

    def __init__(self) -> None:
        self.stats = TransportStats()

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection. Must be idempotent-safe to call once."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection and release resources. Safe to call repeatedly."""

    @abstractmethod
    async def request(self, frame: Frame) -> Frame:
        """Send a request frame and await the correlated response frame."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport currently holds an open connection."""

    async def __aenter__(self) -> Transport:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    def describe(self) -> dict[str, Any]:
        """Return a non-sensitive description of the transport for diagnostics."""
        return {"type": type(self).__name__, "connected": self.is_connected}
