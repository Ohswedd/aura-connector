"""Deterministic schema representation derived from models.

A :class:`ModelSchema` is the compiled, backend-independent description of a model:
its fields, types, primary key, indexes, relationships, and vector metadata. The
serialized form is deterministic (stable key ordering) so it can be committed to a
repository and diffed in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["FieldSchema", "ModelSchema", "RelationshipSchema"]


def _type_name(python_type: Any) -> str:
    if python_type is None:
        return "any"
    if isinstance(python_type, type):
        return python_type.__name__
    return str(python_type)


@dataclass(frozen=True)
class RelationshipSchema:
    """Describes a relationship between two models."""

    name: str
    target: str
    kind: str  # "link" (to-one) or "collection" (to-many)
    on_delete: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "target": self.target, "kind": self.kind}
        if self.on_delete is not None:
            data["on_delete"] = self.on_delete
        return data


@dataclass(frozen=True)
class FieldSchema:
    """Describes a single scalar/document/vector field."""

    name: str
    type: str
    nullable: bool = False
    primary_key: bool = False
    unique: bool = False
    index: bool = False
    full_text: bool = False
    container: str = "none"
    vector_dim: int | None = None
    vector_index: str | None = None
    has_default: bool = False
    description: str | None = None
    alias: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "primary_key": self.primary_key,
            "unique": self.unique,
            "index": self.index,
            "container": self.container,
            "has_default": self.has_default,
        }
        if self.full_text:
            data["full_text"] = True
        if self.vector_dim is not None:
            data["vector_dim"] = self.vector_dim
        if self.vector_index is not None:
            data["vector_index"] = self.vector_index
        if self.description is not None:
            data["description"] = self.description
        if self.alias is not None:
            data["alias"] = self.alias
        return data


@dataclass(frozen=True)
class ModelSchema:
    """Compiled schema for one model."""

    name: str
    fields: tuple[FieldSchema, ...]
    relationships: tuple[RelationshipSchema, ...] = ()
    primary_key: str | None = None

    _by_name: dict[str, FieldSchema] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        mapping = {f.name: f for f in self.fields}
        object.__setattr__(self, "_by_name", mapping)

    def field(self, name: str) -> FieldSchema | None:
        return self._by_name.get(name)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def indexes(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.index or f.unique or f.primary_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "primary_key": self.primary_key,
            "fields": [f.to_dict() for f in self.fields],
            "relationships": [r.to_dict() for r in self.relationships],
        }
