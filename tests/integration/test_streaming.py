"""Streaming contract tests: bounded cursor/page iteration.

These exercise the real client→cursor→reference-server path. The reference backend
materializes internally but hands out one bounded page at a time behind a server cursor
token, so these tests prove the public streaming contract — bounded iteration, batch
sizing, early-cancellation cleanup, and metrics — without any external AuraDB.
"""

from __future__ import annotations

import pytest

from aura import AuraModel, Field
from aura.client import Client
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.transport.memory import MemoryTransport, ReferenceServer


class Event(AuraModel):
    id: int = Field(primary_key=True)
    name: str


@pytest.fixture
async def stream_client():
    server = ReferenceServer()
    transport = MemoryTransport(server)
    client = Client(parse_dsn("aura+memory://localhost/test"), transport, [Event])
    await client._open()
    try:
        yield client, server
    finally:
        await client.close()


async def _seed(client: Client, count: int) -> None:
    await client.bulk_insert(Event, [Event(id=i, name=f"e{i}") for i in range(count)])


async def test_stream_returns_all_records(stream_client) -> None:
    client, server = stream_client
    await _seed(client, 25)
    seen = [e async for e in client.query(Event).stream(batch_size=10)]
    assert [e.id for e in seen] == list(range(25))
    # The server cursor is fully drained and released once iteration completes.
    assert server.open_cursor_count == 0


async def test_stream_respects_batch_size(stream_client) -> None:
    client, server = stream_client
    await _seed(client, 20)
    # While paging, at most one cursor is open and the client receives bounded pages.
    max_open = 0
    seen = 0
    async for _ in client.query(Event).stream(batch_size=5):
        seen += 1
        max_open = max(max_open, server.open_cursor_count)
    assert seen == 20
    assert max_open == 1  # exactly one server cursor held while paging


async def test_invalid_batch_size_raises(stream_client) -> None:
    client, _ = stream_client
    await _seed(client, 3)
    with pytest.raises(AuraQueryError):
        client.query(Event).stream(batch_size=0)
    with pytest.raises(AuraQueryError):
        client.query(Event).stream(batch_size=-5)


async def test_single_page_needs_no_server_cursor(stream_client) -> None:
    client, server = stream_client
    await _seed(client, 3)
    seen = [e async for e in client.query(Event).stream(batch_size=10)]
    assert len(seen) == 3
    # Whole result fit in the first page: no cursor was ever registered.
    assert server.open_cursor_count == 0


async def test_early_break_releases_cursor(stream_client) -> None:
    client, server = stream_client
    await _seed(client, 50)
    got = []
    agen = client.query(Event).stream(batch_size=5)
    async for e in agen:
        got.append(e)
        if len(got) == 7:
            break
    await agen.aclose()
    assert len(got) == 7
    # Early break releases the open server cursor and records a cancellation.
    assert server.open_cursor_count == 0
    assert client.metrics.stream_cancellations == 1


async def test_client_usable_after_early_break(stream_client) -> None:
    client, _ = stream_client
    await _seed(client, 30)
    agen = client.query(Event).stream(batch_size=4)
    async for _ in agen:
        break
    await agen.aclose()
    # The client remains fully usable for further queries after an early break.
    assert await client.query(Event).count() == 30
    rows = await client.query(Event).where(Event.id == 1).all()
    assert rows[0].id == 1


async def test_stream_cancellation_metric_increments(stream_client) -> None:
    client, _ = stream_client
    await _seed(client, 40)
    for _ in range(3):
        agen = client.query(Event).stream(batch_size=2)
        async for _ in agen:
            break
        await agen.aclose()
    assert client.metrics.stream_cancellations == 3


async def test_full_consumption_records_no_cancellation(stream_client) -> None:
    client, _ = stream_client
    await _seed(client, 12)
    seen = [e async for e in client.query(Event).stream(batch_size=5)]
    assert len(seen) == 12
    assert client.metrics.stream_cancellations == 0
