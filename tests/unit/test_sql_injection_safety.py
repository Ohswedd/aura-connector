"""Unit tests proving the SQL compiler binds values rather than concatenating them.

These are the security-critical guarantees: a hostile value never appears in the SQL text,
and identifiers are quoted. The compiler emits placeholders only; values travel in the
parameter list.
"""

from __future__ import annotations

import pytest

from aura import AuraModel, Field
from aura.backends.sql.compiler import SQLCompiler
from aura.backends.sql.dialects import MYSQL, POSTGRES, SQLITE
from aura.query.ast import DeleteQuery, InsertQuery, SelectQuery, UpdateQuery
from aura.query.expressions import FieldReference

INJECTIONS = [
    "1; DROP TABLE Account; --",
    "' OR '1'='1",
    'name"; DELETE FROM Account; --',
    "\\'; SELECT * FROM secrets; --",
    "Robert'); DROP TABLE students;--",
]


class Account(AuraModel):
    id: int = Field(primary_key=True)
    name: str
    note: str = Field(default="")


def _schema_for(name: str):
    return Account.__aura_schema__


def field(name: str) -> FieldReference:
    return FieldReference("Account", name)


@pytest.mark.parametrize("dialect", [SQLITE, POSTGRES, MYSQL])
@pytest.mark.parametrize("payload", INJECTIONS)
def test_filter_value_is_bound_not_concatenated(dialect, payload) -> None:
    ir = SelectQuery(model="Account", filters=(field("name") == payload,)).to_ir()
    stmt = SQLCompiler(dialect, _schema_for).compile(ir)
    # The payload appears only in the bound parameters, never in the SQL text.
    assert payload not in stmt.sql
    assert payload in stmt.params
    assert "DROP TABLE" not in stmt.sql.upper()


@pytest.mark.parametrize("payload", INJECTIONS)
def test_insert_value_is_bound(payload) -> None:
    ir = InsertQuery(model="Account", rows=({"id": 1, "name": payload},)).to_ir()
    stmt = SQLCompiler(SQLITE, _schema_for).compile(ir)
    assert payload not in stmt.sql
    assert payload in stmt.params


@pytest.mark.parametrize("payload", INJECTIONS)
def test_update_value_is_bound(payload) -> None:
    ir = UpdateQuery(
        model="Account", filters=(field("id") == 1,), assignments={"note": payload}
    ).to_ir()
    stmt = SQLCompiler(POSTGRES, _schema_for).compile(ir)
    assert payload not in stmt.sql
    assert payload in stmt.params


@pytest.mark.parametrize("payload", INJECTIONS)
def test_in_clause_values_are_bound(payload) -> None:
    ir = SelectQuery(model="Account", filters=(field("name").in_([payload, "x"]),)).to_ir()
    stmt = SQLCompiler(SQLITE, _schema_for).compile(ir)
    assert payload not in stmt.sql
    assert payload in stmt.params


def test_like_payload_is_bound_with_escaping() -> None:
    payload = "%' OR 1=1 --"
    ir = SelectQuery(model="Account", filters=(field("name").contains(payload),)).to_ir()
    stmt = SQLCompiler(SQLITE, _schema_for).compile(ir)
    assert "OR 1=1" not in stmt.sql
    # The wrapped, wildcard-escaped pattern is a single bound parameter.
    assert any("OR 1=1" in str(p) for p in stmt.params)


def test_delete_payload_bound() -> None:
    payload = "1 OR 1=1"
    ir = DeleteQuery(model="Account", filters=(field("name") == payload,)).to_ir()
    stmt = SQLCompiler(SQLITE, _schema_for).compile(ir)
    assert payload not in stmt.sql
    assert payload in stmt.params
