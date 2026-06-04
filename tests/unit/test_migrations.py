"""Tests for schema diffing and migration planning.

These tests drive ``diff_schemas`` with explicit schema-document dicts (the form
produced by :func:`aura.schema.schema_document`) so the diff logic is exercised directly
and deterministically, plus one end-to-end test through real compiled models.
"""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector, diff_schemas, generate_migration
from aura.errors import AuraMigrationError


def _field(name: str, type_: str = "str", **attrs: object) -> dict:
    base = {
        "name": name,
        "type": type_,
        "nullable": False,
        "primary_key": False,
        "unique": False,
        "index": False,
        "container": "none",
        "has_default": False,
    }
    base.update(attrs)
    return base


def _model(name: str, fields: list[dict], relationships: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "primary_key": "id",
        "fields": fields,
        "relationships": relationships or [],
    }


def _doc(*models: dict) -> dict:
    return {"format_version": 1, "models": list(models)}


USER_V1 = _model(
    "User",
    [
        _field("id", "int", primary_key=True, index=True),
        _field("name"),
        _field("email", index=True),
    ],
)
USER_V2 = _model(
    "User",
    [
        _field("id", "int", primary_key=True, index=True),
        _field("name"),
        _field("email", unique=True, index=True),  # added unique
        _field("nickname", nullable=True),  # additive nullable
        _field("score", "int", has_default=True),  # additive with default
    ],
)


def test_no_change_is_empty_plan() -> None:
    plan = diff_schemas(_doc(USER_V1), _doc(USER_V1))
    assert plan.is_empty
    assert not plan.is_destructive
    assert "No schema changes" in plan.format()


def test_additive_changes_are_non_destructive() -> None:
    plan = diff_schemas(_doc(USER_V1), _doc(USER_V2))
    assert not plan.is_empty
    kinds = {(c.kind, c.target) for c in plan.changes}
    assert ("add_field", "nickname") in kinds
    assert ("add_field", "score") in kinds
    assert ("add_index", "email") in kinds
    assert not plan.is_destructive
    plan.require_safe()  # must not raise


def test_add_required_field_without_default_is_destructive() -> None:
    new = _model(
        "User",
        [*USER_V1["fields"], _field("country", "str")],  # NOT NULL, no default
    )
    plan = diff_schemas(_doc(USER_V1), _doc(new))
    change = next(c for c in plan.changes if c.target == "country")
    assert change.destructive
    assert "NOT NULL" in change.detail


def test_drop_field_is_destructive() -> None:
    plan = diff_schemas(_doc(USER_V2), _doc(USER_V1))
    drops = [c for c in plan.changes if c.kind == "drop_field"]
    assert {c.target for c in drops} == {"nickname", "score"}
    assert all(c.destructive for c in drops)
    assert plan.is_destructive
    with pytest.raises(AuraMigrationError):
        plan.require_safe()


def test_drop_index_is_destructive() -> None:
    plan = diff_schemas(_doc(USER_V2), _doc(USER_V1))
    drop_index = [c for c in plan.changes if c.kind == "drop_index"]
    assert any(c.target == "email" for c in drop_index)
    assert all(c.destructive for c in drop_index)


def test_drop_model_is_destructive() -> None:
    plan = diff_schemas(_doc(USER_V1), _doc())
    assert [c.kind for c in plan.changes] == ["drop_model"]
    assert plan.is_destructive
    assert plan.changes[0].rollback.startswith("recreate model")


def test_add_model_is_non_destructive() -> None:
    plan = diff_schemas(_doc(), _doc(USER_V1))
    assert [c.kind for c in plan.changes] == ["add_model"]
    assert not plan.is_destructive


def test_vector_dim_change_is_destructive() -> None:
    a = _model(
        "Doc", [_field("id", "int", primary_key=True), _field("emb", "Vector", vector_dim=4)]
    )
    b = _model(
        "Doc", [_field("id", "int", primary_key=True), _field("emb", "Vector", vector_dim=8)]
    )
    plan = diff_schemas(_doc(a), _doc(b))
    altered = [c for c in plan.changes if c.kind == "alter_field"]
    assert any("vector_dim" in c.detail for c in altered)
    assert plan.is_destructive


def test_type_change_is_destructive() -> None:
    a = _model("T", [_field("id", "int", primary_key=True), _field("v", "str")])
    b = _model("T", [_field("id", "int", primary_key=True), _field("v", "int")])
    plan = diff_schemas(_doc(a), _doc(b))
    assert plan.is_destructive
    assert any("type" in c.detail for c in plan.changes)


def test_not_null_to_nullable_is_safe() -> None:
    a = _model("T", [_field("id", "int", primary_key=True), _field("v", "str")])
    b = _model("T", [_field("id", "int", primary_key=True), _field("v", "str", nullable=True)])
    plan = diff_schemas(_doc(a), _doc(b))
    assert not plan.is_destructive
    assert any(c.detail == "not null -> nullable" for c in plan.changes)


def test_relationship_add_and_drop() -> None:
    base = _model("Order", [_field("id", "int", primary_key=True)])
    with_rel = _model(
        "Order",
        [_field("id", "int", primary_key=True)],
        relationships=[{"name": "user", "target": "User", "kind": "link"}],
    )
    add = diff_schemas(_doc(base), _doc(with_rel))
    assert any(c.kind == "add_relationship" for c in add.changes)
    assert not add.is_destructive
    drop = diff_schemas(_doc(with_rel), _doc(base))
    assert any(c.kind == "drop_relationship" and c.destructive for c in drop.changes)


def test_rollback_plan_is_reverse_order() -> None:
    plan = diff_schemas(_doc(USER_V1), _doc(USER_V2))
    assert plan.rollback_plan() == [c.rollback for c in reversed(plan.changes)]


def test_diff_is_deterministic() -> None:
    a = diff_schemas(_doc(USER_V1), _doc(USER_V2))
    b = diff_schemas(_doc(USER_V1), _doc(USER_V2))
    assert a.to_dict() == b.to_dict()
    assert a.format() == b.format()


def test_format_flags_destructive() -> None:
    text = diff_schemas(_doc(USER_V2), _doc(USER_V1)).format()
    assert "DESTRUCTIVE" in text
    assert "Rollback" in text
    assert "lock/impact estimates require a live connection" in text


def test_invalid_schema_input_raises() -> None:
    with pytest.raises(AuraMigrationError):
        diff_schemas(42, _doc(USER_V1))
    with pytest.raises(AuraMigrationError):
        diff_schemas([{"no_name": 1}], _doc())


def test_generate_migration_through_real_models() -> None:
    """End-to-end: compile two real model versions and diff them."""

    def v1() -> type[AuraModel]:
        class Account(AuraModel):
            id: int = Field(primary_key=True)
            name: str

        return Account

    def v2() -> type[AuraModel]:
        class Account(AuraModel):
            id: int = Field(primary_key=True)
            name: str
            embedding: Vector[16] | None = None  # additive

        return Account

    plan = generate_migration([v1()], [v2()])
    assert not plan.is_empty
    assert any(c.kind == "add_field" and c.target == "embedding" for c in plan.changes)
    assert not plan.is_destructive


# -- live lock/impact estimate boundary ----------


def _two_model_docs() -> tuple[dict, dict]:
    old = {
        "models": [
            {
                "name": "User",
                "primary_key": "id",
                "fields": [{"name": "id", "type": "int"}, {"name": "email", "type": "str"}],
                "relationships": [],
            }
        ]
    }
    new = {"models": []}  # drop the model -> destructive
    return old, new


def test_local_plan_marks_estimate_local_only() -> None:
    old, new = _two_model_docs()
    plan = diff_schemas(old, new)
    assert plan.impact_estimate_status == "local_only"
    assert plan.lock_impact_estimate is None
    assert plan.requires_live_cluster_estimate is True


def test_empty_plan_needs_no_estimate() -> None:
    old, _ = _two_model_docs()
    plan = diff_schemas(old, old)
    assert plan.is_empty
    assert plan.requires_live_cluster_estimate is False


def test_plan_to_dict_exposes_estimate_fields() -> None:
    old, new = _two_model_docs()
    d = diff_schemas(old, new).to_dict()
    assert d["impact_estimate_status"] == "local_only"
    assert d["requires_live_cluster_estimate"] is True
    assert d["lock_impact_estimate"] is None


def test_with_server_estimate_attaches_structured_estimate() -> None:
    from aura import LockImpactEstimate

    old, new = _two_model_docs()
    plan = diff_schemas(old, new)
    est = LockImpactEstimate(
        lock_level="exclusive", blocking=True, estimated_duration_ms=1500, affected_rows=10_000
    )
    enriched = plan.with_server_estimate(est)
    assert enriched.impact_estimate_status == "server_estimated"
    assert enriched.requires_live_cluster_estimate is False
    assert enriched.lock_impact_estimate is not None
    assert enriched.lock_impact_estimate.lock_level == "exclusive"
    assert enriched.to_dict()["lock_impact_estimate"]["affected_rows"] == 10_000
    # The original plan is unchanged (frozen, copy-on-write).
    assert plan.impact_estimate_status == "local_only"


def test_mark_estimate_unavailable() -> None:
    old, new = _two_model_docs()
    plan = diff_schemas(old, new).mark_estimate_unavailable()
    assert plan.impact_estimate_status == "unavailable"
    assert plan.lock_impact_estimate is None
    assert plan.requires_live_cluster_estimate is True


def test_invalid_estimate_status_rejected() -> None:
    from aura import MigrationPlan
    from aura.errors import AuraMigrationError

    with pytest.raises(AuraMigrationError):
        MigrationPlan(impact_estimate_status="bogus")


def test_estimate_without_server_status_rejected() -> None:
    from aura import LockImpactEstimate, MigrationPlan
    from aura.errors import AuraMigrationError

    est = LockImpactEstimate(lock_level="none", blocking=False)
    with pytest.raises(AuraMigrationError):
        MigrationPlan(impact_estimate_status="local_only", lock_impact_estimate=est)


def test_format_states_estimate_is_local_only() -> None:
    old, new = _two_model_docs()
    text = diff_schemas(old, new).format()
    assert "Lock/impact estimate: local_only" in text
    assert "require a live connection" in text
