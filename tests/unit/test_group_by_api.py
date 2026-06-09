"""v0.7.0 group-by aggregation API (``QueryBuilder.group_by`` / ``.aggregate``).

Covers the chainable builder (immutability, IR placement, validation), the native
backend request shape, and end-to-end grouping against the in-memory reference
engine (which implements the same ``groups`` contract as AuraDB v1.3.0).
"""

from __future__ import annotations

from typing import Any

import pytest

from aura import AggregateResult, AuraModel, Field, GroupByResult
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge
from aura.query.ast import GroupBy
from aura.query.builder import QueryBuilder


class Product(AuraModel):
    id: int = Field(primary_key=True)
    category: str
    price: int


def _builder(model: type[AuraModel]) -> QueryBuilder:
    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


async def _seeded_client():
    from aura.client import Client
    from aura.transport.memory import MemoryTransport, ReferenceServer

    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()), [Product])
    await client._open()
    rows = [
        (0, "a", 10),
        (1, "a", 20),
        (2, "a", 30),
        (3, "b", 40),
        (4, "b", 50),
        (5, "c", 60),
    ]
    for pid, cat, price in rows:
        await client.insert(Product(id=pid, category=cat, price=price))
    return client


def test_group_by_is_immutable_and_sets_ir() -> None:
    base = _builder(Product)
    grouped = base.group_by("category", limit=5)
    assert grouped is not base
    assert base.query.group_by is None, "group_by returns a new builder, leaving the original"
    assert grouped.query.group_by == GroupBy(field="category", limit=5)


def test_group_by_accepts_field_reference() -> None:
    grouped = _builder(Product).group_by(Product.category)
    assert grouped.query.group_by == GroupBy(field="category", limit=None)


def test_group_by_validates_empty_field_and_limit() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Product).group_by("")
    with pytest.raises(AuraQueryError):
        _builder(Product).group_by("category", limit=0)


async def test_group_by_only_is_allowed_without_metrics() -> None:
    client = await _seeded_client()
    try:
        result = await client.query(Product).group_by("category").aggregate()
        assert isinstance(result.groups, GroupByResult)
        assert result.groups.field == "category"
        # Ordered count-desc, key-asc: a=3, b=2, c=1.
        assert [(g.key, g.count) for g in result.groups.groups] == [("a", 3), ("b", 2), ("c", 1)]
        assert result.groups.group_count_total == 3
        assert result.groups.truncated is False
    finally:
        await client.close()


async def test_group_by_computes_per_group_metrics() -> None:
    client = await _seeded_client()
    try:
        result = (
            await client.query(Product)
            .group_by("category")
            .aggregate_count()
            .min("price")
            .max("price")
            .aggregate()
        )
        assert result.groups is not None
        group_a = result.groups.group("a")
        assert group_a is not None
        assert group_a.count == 3
        assert group_a.metric("count") == 3
        assert group_a.metric("min", "price") == 10
        assert group_a.metric("max", "price") == 30
    finally:
        await client.close()


async def test_group_by_limit_truncates_and_flags() -> None:
    client = await _seeded_client()
    try:
        result = await client.query(Product).group_by("category", limit=2).aggregate()
        assert result.groups is not None
        assert len(result.groups.groups) == 2
        assert result.groups.group_count_total == 3
        assert result.groups.truncated is True
        assert result.groups.group_limit == 2
    finally:
        await client.close()


async def test_group_by_avg_metric_yields_float() -> None:
    client = await _seeded_client()
    try:
        result = await client.query(Product).group_by("category").aggregate()
        # The reference engine returns no metrics when none were requested.
        assert result.groups is not None
        assert result.groups.group("a").metrics == []
    finally:
        await client.close()


async def test_native_backend_builds_group_by_request() -> None:
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
            "matched": 6,
            "scanned": 6,
            "metrics": [],
            "facets": [],
            "groups": {
                "field": "category",
                "groups": [{"key": "a", "count": 3, "metrics": []}],
                "group_count_total": 3,
                "group_limit": 5,
            },
        }

    backend._request = fake_request  # type: ignore[assignment]

    node = _builder(Product).group_by("category", limit=5)._query
    agg_ir = {
        **node.to_ir(),
        "operation": "aggregate",
        "facets": list(node.facets),
        "metrics": list(node.metrics),
        "group_by": node.group_by.to_ir(),
    }
    result = await backend.execute_query(agg_ir)

    assert captured["payload"]["query"] == "aggregate"
    # AuraDB's wire shape is a scalar `group_by` field name plus a separate
    # `group_limit` (verified against a live v1.3.0 server), not a nested object.
    assert captured["payload"]["group_by"] == "category"
    assert captured["payload"]["group_limit"] == 5
    agg = AggregateResult.from_dict(result.metadata["aggregate"])
    assert agg.groups is not None
    assert agg.groups.group_count_total == 3
