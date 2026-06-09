"""v0.7.0 ``HnswOptions`` for the approximate-vector (HNSW) preview.

Covers construction/validation, IR serialization (including ``fallback``), and
that ``search_vector`` accepts an ``HnswOptions`` as its ``approximate=`` argument
alongside the existing ``True`` / dict forms.
"""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, HnswOptions, Vector
from aura.errors import AuraQueryError
from aura.query.builder import QueryBuilder


class Item(AuraModel):
    id: int = Field(primary_key=True)
    embedding: Vector[3]


def _builder(model: type[AuraModel]) -> QueryBuilder:
    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


def test_default_fallback_is_exact() -> None:
    assert HnswOptions().fallback == "exact"
    assert HnswOptions().to_ir() == {"fallback": "exact"}


def test_serializes_all_params_and_fallback() -> None:
    opts = HnswOptions(m=16, ef_construction=200, ef_search=64, fallback="error")
    assert opts.to_ir() == {
        "m": 16,
        "ef_construction": 200,
        "ef_search": 64,
        "fallback": "error",
    }


def test_omitted_params_are_not_serialized() -> None:
    assert HnswOptions(ef_search=40).to_ir() == {"ef_search": 40, "fallback": "exact"}


def test_invalid_fallback_rejected() -> None:
    with pytest.raises(AuraQueryError):
        HnswOptions(fallback="approximate")  # type: ignore[arg-type]


def test_non_positive_params_rejected() -> None:
    with pytest.raises(AuraQueryError):
        HnswOptions(m=0)
    with pytest.raises(AuraQueryError):
        HnswOptions(ef_search=-1)
    with pytest.raises(AuraQueryError):
        HnswOptions(ef_construction=True)  # type: ignore[arg-type]


def test_search_vector_accepts_hnsw_options() -> None:
    ir = (
        _builder(Item)
        .search_vector(
            "embedding",
            [1.0, 0.0, 0.0],
            approximate=HnswOptions(m=8, ef_search=40, fallback="error"),
        )
        .query.to_ir()
    )
    assert ir["vector_ann"] == {"m": 8, "ef_search": 40, "fallback": "error"}


def test_search_vector_dict_supports_fallback() -> None:
    ir = (
        _builder(Item)
        .search_vector("embedding", [1.0, 0.0, 0.0], approximate={"fallback": "error"})
        .query.to_ir()
    )
    assert ir["vector_ann"] == {"fallback": "error"}


def test_search_vector_dict_rejects_bad_fallback() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate={"fallback": "x"})


def test_search_vector_true_still_emits_empty_options() -> None:
    ir = _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate=True).query.to_ir()
    assert ir["vector_ann"] == {}


def test_native_backend_passes_fallback_through() -> None:
    from aura.backends.auradb_native import _translate_select

    ir = (
        _builder(Item)
        .search_vector("embedding", [1.0, 0.0, 0.0], approximate=HnswOptions(ef_search=50))
        .query.to_ir()
    )
    server = _translate_select(ir)
    assert server["vector_ann"] == {"ef_search": 50, "fallback": "exact"}
