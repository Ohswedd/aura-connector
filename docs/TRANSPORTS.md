# Transports

A transport moves protocol frames between the client and a server and correlates each
request with its response. The interface (`aura.transport.base.Transport`) is small so a
future QUIC transport can satisfy it unchanged.

```python
class Transport(ABC):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def request(self, frame: Frame) -> Frame: ...
    @property
    def is_connected(self) -> bool: ...
```

Every transport tracks `TransportStats` (frames/bytes sent and received) for
observability.

## MemoryTransport + ReferenceServer

`MemoryTransport` wraps an in-process `ReferenceServer`. Requests are encoded to wire
bytes, decoded by the server, executed against an in-memory store, and the response is
re-encoded and decoded, the **full** protocol + hydration path runs in-process, so
tests and examples exercise the same code as a network transport.

The reference server implements:

- ping, handshake, schema registration/fetch,
- select with filtering, ordering, pagination, projection, and includes,
- vector nearest-neighbour (cosine / euclidean / dot) and hybrid text+vector fusion,
- insert / bulk insert / update / delete / upsert with constraint checks,
- graph traversal with a depth bound,
- transactions via a staged overlay (begin / commit / rollback),
- server cursors for streaming (`CURSOR_FETCH` / `CURSOR_CLOSE`),
- a bounded raw-statement subset with bound parameters.

### Streaming: reference chunking vs. real server cursors

The reference server implements the cursor/page opcodes faithfully: on a cursor request it
materializes the matching rows **in memory**, returns the first bounded page plus an opaque
cursor token, and holds the remainder server-side until the client fetches the next page or
closes the cursor (`open_cursor_count` reports how many are live). The client therefore
holds at most one page at a time and the cursor lifecycle (open → fetch → close, with
early-cancel release) is exercised end-to-end.

This is **reference-backend chunking behind a real server-cursor contract**, not true
server-side streaming: a live AuraDB server would stream cursor pages from storage rather
than materialize first. The client-side bounded-iteration and cancellation contract is
identical in both cases.

```python
from aura.transport.memory import MemoryTransport, ReferenceServer

server = ReferenceServer()
async with MemoryTransport(server) as transport:
    ...
```

Selected by the `aura+memory://` DSN scheme.

## TCPTransport

`TCPTransport` speaks the protocol over asyncio TCP streams (optionally TLS). A
background read loop decodes frames and resolves the future registered for each request
id, so many requests can be in flight over one connection. It is structurally ready for
a real AuraDB server.

- TLS shape is built from `TLSConfig` (CA, client cert/key, hostname verification).
- Connect and request timeouts come from `ClientConfig`.
- On close, the read task is cancelled, pending requests fail cleanly, and the writer is
  drained and closed, no leaked tasks or connections.

Selected by the `aura://`, `auras://`, and `aura+tcp://` DSN schemes.
`tests/integration/test_tcp_transport.py` drives it against a real loopback server.
