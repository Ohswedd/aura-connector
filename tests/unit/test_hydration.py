"""Tests for the hydration layer."""

from __future__ import annotations

import uuid

import pytest

from aura import AuraModel, Field, Vector
from aura.errors import AuraValidationError, RelationshipNotLoadedError
from aura.hydration import Hydrator, hydrate_rows


class Keyed(AuraModel):
    id: uuid.UUID = Field(primary_key=True)
    count: int


class City(AuraModel):
    id: int = Field(primary_key=True)
    name: str


class Citizen(AuraModel):
    id: int = Field(primary_key=True)
    city: City = Field(link=True)
    name: str
    nickname: str | None = Field(default=None)
    scores: list[int] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    embedding: Vector[2] | None = None


def test_hydrate_scalar_row() -> None:
    obj = Hydrator().hydrate_row(Citizen, {"id": 1, "city": 5, "name": "Ann"})
    assert obj.id == 1
    assert obj.name == "Ann"
    assert obj.nickname is None


def test_hydrate_vector() -> None:
    obj = Hydrator().hydrate_row(
        Citizen, {"id": 1, "city": 5, "name": "A", "embedding": [1.0, 2.0]}
    )
    assert isinstance(obj.embedding, Vector)
    assert obj.embedding.dimension == 2


def test_hydrate_list_and_dict() -> None:
    obj = Hydrator().hydrate_row(
        Citizen, {"id": 1, "city": 5, "name": "A", "scores": [1, 2], "attrs": {"k": "v"}}
    )
    assert obj.scores == [1, 2]
    assert obj.attrs == {"k": "v"}


def test_bare_fk_is_not_a_loaded_relationship() -> None:
    obj = Hydrator().hydrate_row(Citizen, {"id": 1, "city": 5, "name": "A"})
    with pytest.raises(RelationshipNotLoadedError):
        _ = obj.city


def test_included_nested_object_is_loaded() -> None:
    obj = Hydrator().hydrate_row(
        Citizen, {"id": 1, "city": {"id": 5, "name": "Metropolis"}, "name": "A"}
    )
    assert isinstance(obj.city, City)
    assert obj.city.name == "Metropolis"


def test_missing_required_field_raises() -> None:
    with pytest.raises(AuraValidationError):
        Hydrator().hydrate_row(Citizen, {"id": 1, "city": 5})  # no name


def test_partial_select_skips_unselected() -> None:
    obj = Hydrator().hydrate_row(Citizen, {"id": 1}, partial=True)
    assert obj.id == 1
    with pytest.raises(AttributeError):
        _ = obj.name


def test_score_attached() -> None:
    obj = Hydrator().hydrate_row(Citizen, {"id": 1, "city": 5, "name": "A", "__score__": 0.87})
    assert obj.__score__ == pytest.approx(0.87)


def test_hydrate_rows_helper() -> None:
    rows = hydrate_rows(City, [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
    assert [c.name for c in rows] == ["A", "B"]


def test_type_coercion_from_strings() -> None:
    raw_id = "12345678-1234-5678-1234-567812345678"
    obj = Hydrator().hydrate_row(Keyed, {"id": raw_id, "count": "5"})
    assert isinstance(obj.id, uuid.UUID)
    assert obj.count == 5
