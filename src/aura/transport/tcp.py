"""TCP transport over asyncio streams.

This transport speaks the Aura Wire Protocol over a real TCP (optionally TLS) socket.
A background read loop decodes incoming frames and resolves the future registered for
each request id, so many requests can be in flight concurrently over one connection.
It is structurally ready for a real AuraDB server; when no server is available, tests
use :class:`~aura.transport.memory.MemoryTransport`.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from typing import TYPE_CHECKING

from ..errors import AuraConnectionError, AuraProtocolError, AuraTimeoutError
from ..protocol.codec import encode_frame, try_decode_frame
from ..protocol.frames import Frame
from .base import Transport

if TYPE_CHECKING:
    from ..config import ClientConfig

__all__ = ["TCPTransport"]


class TCPTransport(Transport):
    """Async TCP transport with request/response correlation by request id."""

    def __init__(self, config: ClientConfig) -> None:
        super().__init__()
        self._config = config
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._buffer = bytearray()
        self._connected = False
        self._write_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        tls = self._config.tls
        if not tls.enabled:
            return None
        context = ssl.create_default_context(cafile=tls.ca_cert_path)
        context.check_hostname = tls.verify_hostname
        if not tls.verify_hostname:
            context.verify_mode = ssl.CERT_NONE
        if tls.client_cert_path and tls.client_key_path:
            context.load_cert_chain(tls.client_cert_path, tls.client_key_path)
        return context

    async def connect(self) -> None:
        if self._connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._config.host,
                    self._config.port,
                    ssl=self._build_ssl_context(),
                ),
                timeout=self._config.connect_timeout_s,
            )
        except TimeoutError as exc:
            raise AuraTimeoutError(f"Timed out connecting to {self._config.address}") from exc
        except OSError as exc:
            raise AuraConnectionError(
                f"Failed to connect to {self._config.address}: {exc}"
            ) from exc
        self._connected = True
        self._read_task = asyncio.get_running_loop().create_task(self._read_loop())

    async def close(self) -> None:
        self._connected = False
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None
        self._fail_pending(AuraConnectionError("Transport closed"))

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def request(self, frame: Frame) -> Frame:
        if not self._connected or self._writer is None:
            raise AuraConnectionError("TCPTransport is not connected")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Frame] = loop.create_future()
        self._pending[frame.request_id] = future

        data = encode_frame(
            frame,
            max_payload_bytes=self._config.max_payload_bytes,
            compress=self._config.compression,
            payload_checksum=self._config.payload_checksum,
        )
        async with self._write_lock:
            self._writer.write(data)
            await self._writer.drain()
        self.stats.frames_sent += 1
        self.stats.bytes_sent += len(data)

        try:
            return await asyncio.wait_for(future, timeout=self._config.request_timeout_s)
        except TimeoutError as exc:
            self._pending.pop(frame.request_id, None)
            raise AuraTimeoutError(
                f"Request {frame.request_id} timed out after {self._config.request_timeout_s}s"
            ) from exc

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while self._connected:
                chunk = await self._reader.read(65536)
                if not chunk:
                    self._fail_pending(AuraConnectionError("Connection closed by server"))
                    self._connected = False
                    return
                self._buffer.extend(chunk)
                self.stats.bytes_received += len(chunk)
                self._drain_buffer()
        except asyncio.CancelledError:
            raise
        except AuraProtocolError as exc:
            self._fail_pending(exc)
        except OSError as exc:
            self._fail_pending(AuraConnectionError(f"Read error: {exc}"))

    def _drain_buffer(self) -> None:
        while True:
            frame, consumed = try_decode_frame(
                bytes(self._buffer), max_payload_bytes=self._config.max_payload_bytes
            )
            if frame is None:
                return
            del self._buffer[:consumed]
            self.stats.frames_received += 1
            future = self._pending.pop(frame.request_id, None)
            if future is not None and not future.done():
                future.set_result(frame)
