"""Typing-oriented sanity tests.

These exercise the public API in a typed style and assert the runtime types that the
static annotations promise. The package ships ``py.typed`` so downstream users get full
checker support; ``mypy src`` in the validation gate verifies the library itself.
"""

from __future__ import annotations

from aura import (
    AggregateGroup,
    AuraModel,
    Field,
    GroupByResult,
    HnswOptions,
    Page,
    QueryProfile,
    Vector,
)
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


def test_v07_result_models_are_typed() -> None:
    group = AggregateGroup(key="emea", count=7)
    assert group.count == 7
    assert group.metric("count") is None

    grouped = GroupByResult(field="region", groups=[group], group_count_total=9, group_limit=2)
    assert grouped.truncated is True
    assert grouped.group("emea") is group

    profile = QueryProfile(rows_scanned=10, search_mode="bm25")
    assert profile.rows_scanned == 10
    assert profile.execution_us is None

    page: Page[int] = Page(items=[1, 2], next_cursor="tok", total=5)
    assert page.has_more is True
    items: list[int] = page.items
    assert items == [1, 2]


def test_hnsw_options_is_typed() -> None:
    opts = HnswOptions(m=16, ef_search=64, fallback="error")
    ir: dict[str, object] = opts.to_ir()
    assert ir["fallback"] == "error"


def test_py_typed_marker_present() -> None:
    import importlib.resources

    files = importlib.resources.files("aura")
    assert (files / "py.typed").is_file()
