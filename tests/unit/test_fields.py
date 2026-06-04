"""Tests for the field system."""

from __future__ import annotations

import pytest

from aura.errors import AuraSchemaError
from aura.fields import MISSING, Field, FieldInfo


def test_field_returns_field_info() -> None:
    info = Field(primary_key=True, index=True)
    assert isinstance(info, FieldInfo)
    assert info.primary_key is True
    assert info.index is True


def test_default_and_factory_conflict() -> None:
    with pytest.raises(AuraSchemaError):
        Field(default=1, default_factory=lambda: 2)


def test_primary_key_cannot_be_nullable() -> None:
    with pytest.raises(AuraSchemaError):
        Field(primary_key=True, nullable=True)


def test_invalid_on_delete() -> None:
    with pytest.raises(AuraSchemaError):
        Field(link=True, on_delete="explode")


def test_has_default() -> None:
    assert Field(default=3).has_default is True
    assert Field(default_factory=list).has_default is True
    assert Field().has_default is False


def test_build_default_factory_produces_fresh() -> None:
    info = Field(default_factory=list)
    a = info.build_default()
    b = info.build_default()
    assert a == [] and b == []
    assert a is not b


def test_build_default_missing() -> None:
    assert Field().build_default() is MISSING


def test_storage_name_uses_alias() -> None:
    info = Field(alias="emailAddress")
    info.name = "email"
    assert info.storage_name == "emailAddress"


def test_is_required() -> None:
    plain = Field()
    plain.name = "x"
    assert plain.is_required() is True
    assert Field(default=1).is_required() is False
    assert Field(nullable=True).is_required() is False
