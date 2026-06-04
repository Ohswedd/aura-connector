"""Typing-oriented sanity tests.

These exercise the public API in a typed style and assert the runtime types that the
static annotations promise. The package ships ``py.typed`` so downstream users get full
checker support; ``mypy src`` in the validation gate verifies the library itself.
"""

from __future__ import annotations

from aura import AuraModel, Field, Vector
from aura.query.builder import QueryResult
from aura.query.expressions import Comparison, FieldReference


class Account(AuraModel):
    id: int = Field(primary_key=True)
    balance: int
    label: str | None = Field(default=None)
    embedding: Vector[8] | None = None


def test_field_reference_type() -> None:
    ref = Account.id
    assert isinstance(ref, FieldReference)


def test_comparison_type() -> None:
    predicate = Account.balance >= 100
    assert isinstance(predicate, Comparison)


def test_instance_value_types() -> None:
    account = Account(id=1, balance=500)
    assert isinstance(account.id, int)
    assert isinstance(account.balance, int)
    assert account.label is None


def test_query_result_dataclass() -> None:
    result = QueryResult(rows=[1, 2], count=2, affected=0)
    assert result.rows == [1, 2]
    assert result.count == 2


def test_py_typed_marker_present() -> None:
    import importlib.resources

    files = importlib.resources.files("aura")
    assert (files / "py.typed").is_file()
