"""v0.6.0 aggregations and facets API (``QueryBuilder.facet``/``.aggregate``).

End-to-end against the in-memory reference engine (which implements the same
``aggregate`` contract as AuraDB v1.2.0), plus the native backend request shape
and the builder guards.
"""

from __future__ import annotations

from typing import Any

import pytest

from aura import AggregateResult, AuraModel, Field
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge
from aura.query.builder import QueryBuilder


class Product(AuraModel):
    id: int = Field(primary_key=True)
    category: str
    brand: str
    price: int
    body: str


def _builder(model: type[AuraModel]) -> QueryBuilder:
    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


async def _seeded_client():
    from aura.client import Client
    from aura.transport.memory import MemoryTransport, ReferenceServer

    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()), [Product])
    await client._open()
    rows = [
        (0, "a", "acme", 10, "red running shoe"),
        (1, "a", "acme", 20, "blue running shoe"),
        (2, "a", "bolt", 30, "running shoe laces"),
        (3, "b", "acme", 40, "winter boot"),
        (4, "b", "bolt", 50, "rain boot"),
        (5, "c", "bolt", 60, "wool sock"),
    ]
    for pid, cat, brand, price, body in rows:
        await client.insert(Product(id=pid, category=cat, brand=brand, price=price, body=body))
    return client


async def test_aggregate_count_min_max() -> None:
    client = await _seeded_client()
    try:
        result = await client.query(Product).aggregate_count().min("price").max("price").aggregate()
        assert isinstance(result, AggregateResult)
        assert result.matched == 6
        assert result.metric("count") == 6
        assert result.metric("min", "price") == 10
        assert result.metric("max", "price") == 60
    finally:
        await client.close()


async def test_aggregate_terms_facet_counts_and_order() -> None:
    client = await _seeded_client()
    try:
        result = await client.query(Product).facet("category").aggregate()
        facet = result.facet("category")
        assert facet is not None
        # count desc, value asc: a=3, b=2, c=1
        assert [(b.value, b.count) for b in facet.buckets] == [("a", 3), ("b", 2), ("c", 1)]
    finally:
        await client.close()


async def test_aggregate_facet_limit_and_tie_break() -> None:
    client = await _seeded_client()
    try:
        # brand counts: acme=3, bolt=3 (tie -> value asc).
        result = await client.query(Product).facet("brand", limit=1).aggregate()
        buckets = result.facet("brand").buckets
        assert len(buckets) == 1
        assert buckets[0].value == "acme"
        assert buckets[0].count == 3
    finally:
        await client.close()


async def test_aggregate_with_filter() -> None:
    client = await _seeded_client()
    try:
        result = (
            await client.query(Product)
            .filter(Product.category == "a")
            .aggregate_count()
            .max("price")
            .aggregate()
        )
        assert result.matched == 3
        assert result.filter_present is True
        assert result.metric("count") == 3
        assert result.metric("max", "price") == 30
    finally:
        await client.close()


async def test_search_facet_over_bm25_candidates() -> None:
    client = await _seeded_client()
    try:
        # "running" matches the three category-"a" products.
        result = (
            await client.query(Product)
            .search_text("body", "running")
            .facet("category")
            .aggregate_count()
            .aggregate()
        )
        assert result.search_scoped is True
        assert result.matched == 3
        facet = result.facet("category")
        assert [(b.value, b.count) for b in facet.buckets] == [("a", 3)]
    finally:
        await client.close()


async def test_aggregate_requires_a_facet_or_metric() -> None:
    client = await _seeded_client()
    try:
        with pytest.raises(AuraQueryError):
            await client.query(Product).aggregate()
    finally:
        await client.close()


def test_aggregate_facet_validation() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Product).facet("", limit=10)
    with pytest.raises(AuraQueryError):
        _builder(Product).facet("category", limit=0)


async def test_native_backend_builds_aggregate_request() -> None:
    from aura.backends.auradb_native import AuraDBNativeBackend

    config = parse_dsn("auradb://localhost:7171/db")
    backend = AuraDBNativeBackend(
        config, Metrics(), _TelemetryBridge(TelemetryConfig.from_value(None))
    )

    captured: dict[str, Any] = {}

    async def fake_request(opcode: int, payload_obj: Any, txn_id: int = 0):
        captured["payload"] = payload_obj
        return 0, {
            "collection": "Product",
            "matched": 3,
            "scanned": 6,
            "filter_present": True,
            "search_scoped": False,
            "metrics": [{"op": "count", "value": 3}],
            "facets": [
                {"field": "category", "used_index": True, "buckets": [{"value": "a", "count": 3}]}
            ],
        }

    backend._request = fake_request  # type: ignore[assignment]

    node = (
        _builder(Product)
        .filter(Product.category == "a")
        .facet("category", limit=5)
        .aggregate_count()
        ._query
    )
    agg_ir = {
        **node.to_ir(),
        "operation": "aggregate",
        "facets": list(node.facets),
        "metrics": list(node.metrics),
    }
    result = await backend.execute_query(agg_ir)

    payload = captured["payload"]
    assert payload["query"] == "aggregate"
    assert payload["collection"] == "Product"
    assert payload["facets"] == [{"field": "category", "limit": 5}]
    assert payload["metrics"] == [{"op": "count"}]
    assert "filter" in payload
    # The structured result is surfaced under metadata["aggregate"].
    agg = AggregateResult.from_dict(result.metadata["aggregate"])
    assert agg.matched == 3
    assert agg.metric("count") == 3
    assert agg.facet("category").used_index is True
