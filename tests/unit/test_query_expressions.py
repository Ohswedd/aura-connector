"""Tests for the expression tree."""

from __future__ import annotations

from aura import AuraModel, Field
from aura.query.expressions import (
    BooleanOp,
    Comparison,
    Not,
    and_,
    not_,
    or_,
)


class Item(AuraModel):
    id: int = Field(primary_key=True)
    name: str
    price: int
    metadata: dict[str, str] = Field(default_factory=dict)


def test_comparison_operators_build_nodes() -> None:
    assert isinstance(Item.id == 1, Comparison)
    assert (Item.id == 1).op == "eq"
    assert (Item.id != 1).op == "ne"
    assert (Item.price < 5).op == "lt"
    assert (Item.price <= 5).op == "le"
    assert (Item.price > 5).op == "gt"
    assert (Item.price >= 5).op == "ge"


def test_comparison_ir() -> None:
    ir = (Item.id == 7).to_ir()
    assert ir == {
        "kind": "compare",
        "op": "eq",
        "left": {"kind": "field", "model": "Item", "field": "id"},
        "right": {"kind": "literal", "value": 7},
    }


def test_set_and_string_predicates() -> None:
    assert Item.id.in_([1, 2, 3]).op == "in"
    assert Item.id.not_in([1]).op == "not_in"
    assert Item.name.contains("a").op == "contains"
    assert Item.name.startswith("a").op == "startswith"
    assert Item.name.endswith("z").op == "endswith"
    assert Item.name.like("a%").op == "like"


def test_null_predicates() -> None:
    assert Item.name.is_null().op == "is_null"
    assert Item.name.is_not_null().op == "is_not_null"


def test_document_path_predicate() -> None:
    ref = Item.metadata["source"]
    assert ref.path == ("source",)
    ir = ref.exists().to_ir()
    assert ir["left"]["path"] == ["source"]


def test_boolean_combinators_operators() -> None:
    expr = (Item.id == 1) & (Item.price > 2)
    assert isinstance(expr, BooleanOp)
    assert expr.op == "and"
    assert isinstance((Item.id == 1) | (Item.id == 2), BooleanOp)
    assert isinstance(~(Item.id == 1), Not)


def test_boolean_helper_functions() -> None:
    assert and_(Item.id == 1, Item.id == 2).op == "and"
    assert or_(Item.id == 1, Item.id == 2).op == "or"
    assert isinstance(not_(Item.id == 1), Not)


def test_order_terms() -> None:
    assert Item.id.asc().direction == "asc"
    assert Item.id.desc().direction == "desc"


def test_distance_expressions() -> None:
    assert Item.price.cosine_distance([1, 2]).metric == "cosine"
    assert Item.price.l2_distance([1, 2]).metric == "euclidean"
    assert Item.price.dot_product([1, 2]).metric == "dot"
