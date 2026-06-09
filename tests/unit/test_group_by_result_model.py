"""Parsing of the AuraDB v1.3.0 ``groups`` contract into the result models.

These tests feed the exact JSON shapes the server emits (and older shapes that
omit the new fields) to confirm the models parse defensively.
"""

from __future__ import annotations

from aura import AggregateGroup, AggregateResult, GroupByResult


def test_parses_full_groups_contract() -> None:
    data = {
        "collection": "Order",
        "matched": 12,
        "scanned": 20,
        "metrics": [{"op": "count", "value": 12}],
        "facets": [],
        "groups": {
            "field": "region",
            "groups": [
                {
                    "key": "emea",
                    "count": 7,
                    "metrics": [
                        {"op": "count", "value": 7},
                        {"op": "avg", "field": "amount", "value": 41.5},
                    ],
                },
                {"key": "amer", "count": 5, "metrics": [{"op": "count", "value": 5}]},
            ],
            "group_count_total": 9,
            "group_limit": 2,
        },
    }
    result = AggregateResult.from_dict(data)
    assert isinstance(result.groups, GroupByResult)
    assert result.groups.field == "region"
    assert len(result.groups.groups) == 2
    assert result.groups.group_count_total == 9
    assert result.groups.group_limit == 2
    assert result.groups.truncated is True

    emea = result.groups.group("emea")
    assert isinstance(emea, AggregateGroup)
    assert emea.count == 7
    assert emea.metric("count") == 7
    assert emea.metric("avg", "amount") == 41.5
    assert isinstance(emea.metric("avg", "amount"), float)


def test_avg_value_may_be_null() -> None:
    group = AggregateGroup.from_dict(
        {"key": "x", "count": 0, "metrics": [{"op": "avg", "value": None}]}
    )
    assert group.metric("avg") is None


def test_metrics_omitted_yields_empty_list() -> None:
    group = AggregateGroup.from_dict({"key": "x", "count": 3})
    assert group.metrics == []


def test_group_count_total_defaults_to_len_when_absent() -> None:
    result = GroupByResult.from_dict(
        {"field": "k", "groups": [{"key": 1, "count": 2}, {"key": 2, "count": 1}]}
    )
    assert result.group_count_total == 2
    assert result.truncated is False


def test_old_aggregate_response_has_no_groups() -> None:
    # An AuraDB response that predates group-by simply omits the ``groups`` key.
    result = AggregateResult.from_dict(
        {"collection": "Order", "matched": 3, "metrics": [{"op": "count", "value": 3}]}
    )
    assert result.groups is None
    assert result.metric("count") == 3


def test_scalar_keys_preserved() -> None:
    result = GroupByResult.from_dict(
        {
            "field": "tier",
            "groups": [{"key": 1, "count": 2}, {"key": True, "count": 1}],
            "group_count_total": 2,
            "group_limit": 10,
        }
    )
    assert [g.key for g in result.groups] == [1, True]
