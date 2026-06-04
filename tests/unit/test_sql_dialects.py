"""Unit tests for SQL dialects: parameter styles, quoting, and type mapping."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector
from aura.backends.sql.compiler import SQLCompiler
from aura.backends.sql.dialects import MYSQL, POSTGRES, SQLITE
from aura.errors import AuraDialectError
from aura.query.ast import SelectQuery
from aura.query.expressions import FieldReference


class Item(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(index=True)
    qty: int = Field(default=0)
    ratio: float = Field(default=0.0)
    enabled: bool = Field(default=True)
    tags: list[str] = Field(default_factory=list)
    vec: Vector[3] | None = None


def _schema_for(name: str):
    return Item.__aura_schema__


def _filtered_ir() -> dict:
    return SelectQuery(model="Item", filters=(FieldReference("Item", "id") == 5,)).to_ir()


@pytest.mark.parametrize(
    ("dialect", "placeholder"),
    [(SQLITE, "?"), (POSTGRES, "$1"), (MYSQL, "%s")],
)
def test_parameter_styles(dialect, placeholder) -> None:
    stmt = SQLCompiler(dialect, _schema_for).compile(_filtered_ir())
    assert stmt.sql.endswith(f"= {placeholder}")
    assert stmt.params == [5]


def test_numeric_style_increments() -> None:
    ir = SelectQuery(
        model="Item",
        filters=(FieldReference("Item", "id") == 1, FieldReference("Item", "qty") == 2),
    ).to_ir()
    stmt = SQLCompiler(POSTGRES, _schema_for).compile(ir)
    assert '"id" = $1' in stmt.sql
    assert '"qty" = $2' in stmt.sql


def test_identifier_quoting_per_dialect() -> None:
    assert SQLITE.quote("name") == '"name"'
    assert POSTGRES.quote("name") == '"name"'
    assert MYSQL.quote("name") == "`name`"


def test_identifier_quote_doubles_quote_char() -> None:
    assert SQLITE.quote('we"ird') == '"we""ird"'
    assert MYSQL.quote("we`ird") == "`we``ird`"


def test_identifier_nul_byte_rejected() -> None:
    with pytest.raises(AuraDialectError):
        SQLITE.quote("bad\x00name")


def test_type_mapping_scalars() -> None:
    schema = Item.__aura_schema__
    int_field = schema.field("qty")
    assert SQLITE.column_type(int_field) == "INTEGER"
    assert POSTGRES.column_type(int_field) == "BIGINT"
    bool_field = schema.field("enabled")
    assert SQLITE.column_type(bool_field) == "INTEGER"
    assert POSTGRES.column_type(bool_field) == "BOOLEAN"
    assert MYSQL.column_type(bool_field) == "TINYINT(1)"


def test_type_mapping_json_and_vector_fallback() -> None:
    schema = Item.__aura_schema__
    tags = schema.field("tags")
    vec = schema.field("vec")
    # Containers and vectors fall back to JSON text on every SQL dialect.
    assert SQLITE.is_json_type(tags) and SQLITE.column_type(tags) == "TEXT"
    assert SQLITE.is_json_type(vec) and POSTGRES.column_type(vec) == "TEXT"


def test_mysql_indexed_string_gets_bounded_type() -> None:
    schema = Item.__aura_schema__
    name = schema.field("name")  # index=True
    assert MYSQL.column_type(name) == "VARCHAR(255)"
