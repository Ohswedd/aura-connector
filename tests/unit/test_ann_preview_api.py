"""v0.6.0 approximate-vector (HNSW) preview option on ``search_vector``.

Exact vector search is the default; ``approximate=`` opts into AuraDB v1.2.0's
HNSW preview. Covers IR shape, parameter validation, native-backend passthrough,
and end-to-end execution against the in-memory reference engine.
"""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector
from aura.config import parse_dsn
from aura.errors import AuraQueryError
from aura.query.builder import QueryBuilder


class Item(AuraModel):
    id: int = Field(primary_key=True)
    embedding: Vector[3]


def _builder(model: type[AuraModel]) -> QueryBuilder:
    return QueryBuilder(model, executor=object())  # type: ignore[arg-type]


def test_exact_by_default_no_vector_ann_in_ir() -> None:
    ir = _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0]).query.to_ir()
    assert "vector" in ir
    assert "vector_ann" not in ir, "exact search emits no approximate options"


def test_approximate_true_emits_empty_ann_options() -> None:
    ir = _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate=True).query.to_ir()
    assert ir["vector_ann"] == {}


def test_approximate_params_emitted() -> None:
    ir = (
        _builder(Item)
        .search_vector(
            "embedding",
            [1.0, 0.0, 0.0],
            approximate={"m": 8, "ef_construction": 64, "ef_search": 40},
        )
        .query.to_ir()
    )
    assert ir["vector_ann"] == {"m": 8, "ef_construction": 64, "ef_search": 40}


def test_approximate_param_validation() -> None:
    with pytest.raises(AuraQueryError):
        _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate={"bogus": 1})
    with pytest.raises(AuraQueryError):
        _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate={"m": 0})
    with pytest.raises(AuraQueryError):
        _builder(Item).search_vector("embedding", [1.0, 0.0, 0.0], approximate={"ef_search": -5})


def test_native_backend_passes_vector_ann_through() -> None:
    from aura.backends.auradb_native import _translate_select

    ir = (
        _builder(Item)
        .search_vector("embedding", [1.0, 0.0, 0.0], approximate={"ef_search": 50})
        .query.to_ir()
    )
    server = _translate_select(ir)
    assert server["vector"]["field"] == "embedding"
    assert server["vector_ann"] == {"ef_search": 50}


async def test_approximate_vector_search_end_to_end() -> None:
    from aura.client import Client
    from aura.transport.memory import MemoryTransport, ReferenceServer

    config = parse_dsn("aura+memory://localhost/test")
    client = Client(config, MemoryTransport(ReferenceServer()), [Item])
    await client._open()
    try:
        await client.insert(Item(id=1, embedding=[1.0, 0.0, 0.0]))
        await client.insert(Item(id=2, embedding=[0.0, 1.0, 0.0]))
        await client.insert(Item(id=3, embedding=[0.9, 0.1, 0.0]))

        rows = (
            await client.search(Item)
            .search_vector("embedding", [1.0, 0.0, 0.0], top_k=2, approximate=True)
            .all()
        )
        ids = [r.id for r in rows]
        # The query carried the approximate option and returned ranked results;
        # the nearest vector to [1,0,0] ranks first.
        assert ids[0] == 1
        assert len(ids) == 2
    finally:
        await client.close()


def test_exact_vector_options_unaffected() -> None:
    # The exact path (no `approximate`) is unchanged.
    ir = (
        _builder(Item)
        .search_vector("embedding", [1.0, 0.0, 0.0], metric="dot", top_k=5)
        .query.to_ir()
    )
    assert ir["vector"]["metric"] == "dot"
    assert ir["limit"] == 5
    assert "vector_ann" not in ir
