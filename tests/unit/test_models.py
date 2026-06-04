"""Tests for the model system."""

from __future__ import annotations

import pytest

from aura import AuraModel, Field, Vector
from aura.errors import AuraValidationError, RelationshipNotLoadedError
from aura.query.expressions import FieldReference


class Address(AuraModel):
    id: int = Field(primary_key=True)
    city: str


class Person(AuraModel):
    id: int = Field(primary_key=True)
    name: str
    nickname: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    embedding: Vector[3] | None = None
    address: Address = Field(link=True)


def test_class_access_returns_field_reference() -> None:
    ref = Person.name
    assert isinstance(ref, FieldReference)
    assert ref.model == "Person"
    assert ref.name == "name"


def test_instance_access_returns_value() -> None:
    p = Person(id=1, name="Ann")
    assert p.name == "Ann"
    assert p.nickname is None
    assert p.tags == []


def test_default_factory_is_independent() -> None:
    a = Person(id=1, name="A")
    b = Person(id=2, name="B")
    a.tags.append("x")
    assert b.tags == []


def test_missing_required_field() -> None:
    with pytest.raises(AuraValidationError):
        Person(id=1)


def test_unknown_field_rejected() -> None:
    with pytest.raises(AuraValidationError):
        Person(id=1, name="A", bogus=2)


def test_vector_coercion() -> None:
    p = Person(id=1, name="A", embedding=[1, 2, 3])
    assert isinstance(p.embedding, Vector)
    assert p.embedding.dimension == 3


def test_vector_dimension_validation() -> None:
    with pytest.raises(AuraValidationError):
        Person(id=1, name="A", embedding=[1, 2])


def test_unloaded_relationship_raises() -> None:
    p = Person(id=1, name="A")
    with pytest.raises(RelationshipNotLoadedError):
        _ = p.address


def test_loaded_relationship_from_dict() -> None:
    p = Person(id=1, name="A", address={"id": 9, "city": "NYC"})
    assert isinstance(p.address, Address)
    assert p.address.city == "NYC"


def test_to_dict_serialization() -> None:
    p = Person(id=1, name="A", embedding=[1, 2, 3], attrs={"k": "v"})
    data = p.to_dict()
    assert data["id"] == 1
    assert data["embedding"] == [1.0, 2.0, 3.0]
    assert data["attrs"] == {"k": "v"}
    # unloaded relationship omitted
    assert "address" not in data


def test_primary_key_value() -> None:
    assert Person(id=42, name="A").primary_key_value() == 42


def test_aura_construct_bypasses_init_and_runs_hook() -> None:
    class Hooked(AuraModel):
        id: int = Field(primary_key=True)
        seen: bool = Field(default=False)

        def __aura_post_load__(self) -> None:
            self.__dict__["__aura_values__"]["seen"] = True

    obj = Hooked._aura_construct({"id": 1, "seen": False}, set())
    assert obj.seen is True


def test_equality_and_hash() -> None:
    a = Person(id=1, name="A")
    b = Person(id=1, name="A")
    assert a == b
    assert hash(a) == hash(b)


def test_alias_round_trips() -> None:
    class Aliased(AuraModel):
        id: int = Field(primary_key=True)
        display: str = Field(alias="displayName")

    obj = Aliased(id=1, displayName="hi")
    assert obj.display == "hi"
    assert obj.to_dict()["displayName"] == "hi"
