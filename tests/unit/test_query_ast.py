"""Tests for query AST nodes and Query IR."""

from __future__ import annotations

from aura import AuraModel, Field
from aura.query.ast import (
    CountQuery,
    DeleteQuery,
    Include,
    InsertQuery,
    SelectQuery,
    UpdateQuery,
    UpsertQuery,
    VectorSearch,
)
from aura.query.expressions import OrderTerm


class Thing(AuraModel):
    id: int = Field(primary_key=True)
    name: str


def test_select_ir_matches_prd_shape() -> None:
    node = SelectQuery(
        model="User",
        filters=(Thing.id == 1,),
        projection=("id", "name"),
        includes=(Include("orders", limit=20),),
        order_by=(Thing.id.desc(),),
        limit=25,
        offset=5,
        timeout_ms=300,
    )
    ir = node.to_ir()
    assert ir["operation"] == "select"
    assert ir["model"] == "User"
    assert ir["projection"] == ["id", "name"]
    assert ir["include"][0]["link"] == "orders"
    assert ir["include"][0]["limit"] == 20
    assert ir["limit"] == 25
    assert ir["offset"] == 5
    assert ir["consistency"] == "strong"
    assert ir["timeout_ms"] == 300


def test_vector_search_ir() -> None:
    node = SelectQuery(model="Doc", vector=VectorSearch("embedding", (1.0, 0.0), "cosine"))
    ir = node.to_ir()
    assert ir["vector"] == {"field": "embedding", "metric": "cosine", "query": [1.0, 0.0]}


def test_insert_ir() -> None:
    node = InsertQuery(model="User", rows=({"id": 1},), on_conflict="ignore")
    ir = node.to_ir()
    assert ir["operation"] == "insert"
    assert ir["rows"] == [{"id": 1}]
    assert ir["on_conflict"] == "ignore"


def test_update_ir() -> None:
    node = UpdateQuery(model="User", filters=(Thing.id == 1,), assignments={"name": "x"})
    ir = node.to_ir()
    assert ir["operation"] == "update"
    assert ir["set"] == {"name": "x"}


def test_delete_ir() -> None:
    node = DeleteQuery(model="User", filters=(Thing.id == 1,))
    assert node.to_ir()["operation"] == "delete"


def test_upsert_ir() -> None:
    node = UpsertQuery(model="User", key={"email": "a"}, values={"name": "b"})
    ir = node.to_ir()
    assert ir["key"] == {"email": "a"}
    assert ir["values"] == {"name": "b"}


def test_count_ir() -> None:
    assert CountQuery(model="User").to_ir()["operation"] == "count"


def test_order_term_ir() -> None:
    assert OrderTerm(Thing.id, "asc").to_ir() == {"field": "id", "direction": "asc"}


def test_ast_nodes_are_immutable() -> None:
    """AST nodes are frozen dataclasses; mutating one must raise."""
    import dataclasses

    import pytest

    node = SelectQuery(model="User", limit=5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.limit = 10  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.model = "Other"  # type: ignore[misc]

    include = Include(relationship="orders", limit=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        include.limit = 1  # type: ignore[misc]


def test_to_ir_does_not_mutate_node() -> None:
    """Serializing to IR returns copies; the node's collections stay intact."""
    node = InsertQuery(model="User", rows=({"id": 1},))
    ir = node.to_ir()
    ir["rows"].append({"id": 2})
    ir["model"] = "Mutated"
    assert node.rows == ({"id": 1},)
    assert node.to_ir()["rows"] == [{"id": 1}]
    assert node.model == "User"
