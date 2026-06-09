"""Parsing of the AuraDB v1.3.0 best-effort query-profile contract.

The profile is additive and every field optional; these tests confirm the model
tolerates a full profile, a sparse one, an absent one, and odd value types.
"""

from __future__ import annotations

from aura import AggregateResult, QueryProfile


def test_parses_full_profile() -> None:
    data = {
        "plan_id": "p-123",
        "planning_us": 40,
        "execution_us": 900,
        "rows_scanned": 1000,
        "rows_matched": 120,
        "rows_returned": 50,
        "index_used": True,
        "search_mode": "bm25",
        "vector_mode": "ann_preview",
        "facet_buckets": 8,
        "groups_returned": 4,
        "timeout_checked": True,
        "deadline_ms": 250,
        "cursor_mode": "keyset",
        "warnings": ["approximate fallback to exact"],
    }
    profile = QueryProfile.from_dict(data)
    assert profile.plan_id == "p-123"
    assert profile.planning_us == 40
    assert profile.execution_us == 900
    assert profile.rows_scanned == 1000
    assert profile.index_used is True
    assert profile.search_mode == "bm25"
    assert profile.vector_mode == "ann_preview"
    assert profile.facet_buckets == 8
    assert profile.groups_returned == 4
    assert profile.timeout_checked is True
    assert profile.deadline_ms == 250
    assert profile.cursor_mode == "keyset"
    assert profile.warnings == ["approximate fallback to exact"]


def test_sparse_profile_leaves_other_fields_none() -> None:
    profile = QueryProfile.from_dict({"execution_us": 12})
    assert profile.execution_us == 12
    assert profile.plan_id is None
    assert profile.rows_scanned is None
    assert profile.index_used is None
    assert profile.warnings == []


def test_ignores_wrong_typed_values() -> None:
    # A non-int timing or non-bool flag is ignored rather than crashing parsing.
    profile = QueryProfile.from_dict(
        {"planning_us": "fast", "index_used": "yes", "warnings": "oops"}
    )
    assert profile.planning_us is None
    assert profile.index_used is None
    assert profile.warnings == []


def test_aggregate_result_attaches_profile() -> None:
    data = {
        "collection": "Order",
        "matched": 3,
        "metrics": [{"op": "count", "value": 3}],
        "profile": {"rows_scanned": 9, "rows_matched": 3},
    }
    result = AggregateResult.from_dict(data)
    assert isinstance(result.profile, QueryProfile)
    assert result.profile.rows_scanned == 9
    assert result.profile.rows_matched == 3


def test_aggregate_result_without_profile_is_none() -> None:
    result = AggregateResult.from_dict({"collection": "Order", "matched": 0})
    assert result.profile is None
