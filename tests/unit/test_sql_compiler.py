"""Unit tests for the Query IR -> SQL compiler."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field
from aura.backends.sql.compiler import SQLCompiler
from aura.backends.sql.dialects import SQLITE
from aura.errors import AuraBackendCapabilityError, AuraDialectError
from aura.query.ast import (
    CountQuery,
    DeleteQuery,
    ExistsQuery,
    InsertQuery,
    SelectQuery,
    UpdateQuery,
)
from aura.query.expressions import FieldReference, and_, or_


class Widget(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)
    price: float = Field(default=0.0)
    tags: list[str] = Field(default_factory=list)
    active: bool = Field(default=True)


def _schema_for(name: str):
    return Widget.__aura_schema__


def compiler(dialect=SQLITE) -> SQLCompiler:
    return SQLCompiler(dialect, _schema_for)


def field(name: str) -> FieldReference:
    return FieldReference("Widget", name)


def test_select_all() -> None:
    stmt = compiler().compile(SelectQuery(model="Widget").to_ir())
    assert stmt.sql == 'SELECT * FROM "Widget"'
    assert stmt.params == []


def test_select_with_equality_filter_binds_value() -> None:
    ir = SelectQuery(model="Widget", filters=(field("id") == 7,)).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql == 'SELECT * FROM "Widget" WHERE "id" = ?'
    assert stmt.params == [7]


def test_comparisons() -> None:
    ir = SelectQuery(model="Widget", filters=(field("price") >= 10.0,)).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql.endswith('WHERE "price" >= ?')
    assert stmt.params == [10.0]


def test_in_clause_binds_each_value() -> None:
    ir = SelectQuery(model="Widget", filters=(field("id").in_([1, 2, 3]),)).to_ir()
    stmt = compiler().compile(ir)
    assert '"id" IN (?, ?, ?)' in stmt.sql
    assert stmt.params == [1, 2, 3]


def test_empty_in_clause_is_false() -> None:
    ir = SelectQuery(model="Widget", filters=(field("id").in_([]),)).to_ir()
    assert "1 = 0" in compiler().compile(ir).sql


def test_contains_startswith_endswith_bind_patterns() -> None:
    c = compiler()
    contains_ir = SelectQuery(model="Widget", filters=(field("name").contains("ix"),)).to_ir()
    contains = c.compile(contains_ir)
    assert "LIKE ? ESCAPE" in contains.sql
    assert contains.params == ["%ix%"]
    starts_ir = SelectQuery(model="Widget", filters=(field("name").startswith("wi"),)).to_ir()
    assert c.compile(starts_ir).params == ["wi%"]
    ends_ir = SelectQuery(model="Widget", filters=(field("name").endswith("et"),)).to_ir()
    assert c.compile(ends_ir).params == ["%et"]


def test_like_wildcards_in_value_are_escaped() -> None:
    stmt = compiler().compile(
        SelectQuery(model="Widget", filters=(field("name").contains("100%_x"),)).to_ir()
    )
    assert stmt.params == ["%100\\%\\_x%"]


def test_and_or_nesting() -> None:
    predicate = and_(field("price") > 1, or_(field("id") == 2, field("id") == 3))
    stmt = compiler().compile(SelectQuery(model="Widget", filters=(predicate,)).to_ir())
    assert stmt.sql == 'SELECT * FROM "Widget" WHERE ("price" > ? AND ("id" = ? OR "id" = ?))'
    assert stmt.params == [1, 2, 3]


def test_is_null_uses_is_null_not_param() -> None:
    stmt = compiler().compile(SelectQuery(model="Widget", filters=(field("name") == None,)).to_ir())  # noqa: E711
    assert stmt.sql.endswith('WHERE "name" IS NULL')
    assert stmt.params == []


def test_order_limit_offset() -> None:
    ir = SelectQuery(model="Widget", order_by=(field("price").desc(),), limit=10, offset=5).to_ir()
    stmt = compiler().compile(ir)
    assert 'ORDER BY "price" DESC' in stmt.sql
    assert "LIMIT 10" in stmt.sql
    assert "OFFSET 5" in stmt.sql


def test_select_fields_projection_includes_pk() -> None:
    ir = SelectQuery(model="Widget", projection=("name",)).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql.startswith('SELECT "name", "id" FROM "Widget"')


def test_count_and_exists() -> None:
    c = compiler()
    count = c.compile(CountQuery(model="Widget").to_ir())
    assert count.sql == 'SELECT COUNT(*) AS "count" FROM "Widget"'
    exists = c.compile(ExistsQuery(model="Widget", filters=(field("id") == 1,)).to_ir())
    assert exists.sql.startswith("SELECT EXISTS(")
    assert exists.params == [1]


def test_insert_single_row_binds_values_and_returns() -> None:
    ir = InsertQuery(model="Widget", rows=({"id": 1, "name": "a", "price": 2.5},)).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql.startswith('INSERT INTO "Widget" (')
    assert "VALUES (?, ?, ?)" in stmt.sql
    assert stmt.sql.endswith("RETURNING *")
    assert set(stmt.params) == {1, "a", 2.5}


def test_bulk_insert_emits_one_tuple_per_row() -> None:
    rows = ({"id": 1, "name": "a"}, {"id": 2, "name": "b"})
    stmt = compiler().compile(InsertQuery(model="Widget", rows=rows).to_ir())
    assert stmt.sql.count("(?, ?)") == 2
    assert stmt.params == [1, "a", 2, "b"]


def test_list_field_is_json_encoded_on_insert() -> None:
    ir = InsertQuery(model="Widget", rows=({"id": 1, "name": "a", "tags": ["x", "y"]},)).to_ir()
    stmt = compiler().compile(ir)
    assert '["x","y"]' in stmt.params


def test_insert_on_conflict_ignore() -> None:
    ir = InsertQuery(model="Widget", rows=({"id": 1, "name": "a"},), on_conflict="ignore").to_ir()
    assert "ON CONFLICT DO NOTHING" in compiler().compile(ir).sql


def test_update_binds_assignments_then_filters() -> None:
    ir = UpdateQuery(
        model="Widget", filters=(field("id") == 9,), assignments={"price": 3.0}
    ).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql == 'UPDATE "Widget" SET "price" = ? WHERE "id" = ?'
    assert stmt.params == [3.0, 9]


def test_delete_with_filter() -> None:
    ir = DeleteQuery(model="Widget", filters=(field("id") == 4,)).to_ir()
    stmt = compiler().compile(ir)
    assert stmt.sql == 'DELETE FROM "Widget" WHERE "id" = ?'
    assert stmt.params == [4]


def test_vector_search_raises_capability_error() -> None:
    ir = SelectQuery(model="Widget").to_ir()
    ir["vector"] = {"field": "embedding", "metric": "cosine", "query": [1.0]}
    with pytest.raises(AuraBackendCapabilityError):
        compiler().compile(ir)


def test_traverse_raises_capability_error() -> None:
    with pytest.raises(AuraBackendCapabilityError):
        compiler().compile({"operation": "traverse", "model": "Widget"})


def test_document_path_filter_raises_dialect_error() -> None:
    ref = FieldReference("Widget", "meta", ("k",))
    ir = SelectQuery(model="Widget", filters=(ref == "v",)).to_ir()
    with pytest.raises(AuraDialectError):
        compiler().compile(ir)
