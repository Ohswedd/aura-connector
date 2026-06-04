"""Tests for deterministic schema generation."""

from __future__ import annotations

from aura import AuraModel, Field, Vector
from aura.schema import schema_document, schema_json


class Team(AuraModel):
    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)


class Member(AuraModel):
    id: int = Field(primary_key=True)
    team: Team = Field(link=True, on_delete="cascade")
    handle: str
    bio: str | None = Field(default=None)
    embedding: Vector[16] = Field(vector_index="hnsw")


def test_model_schema_fields() -> None:
    schema = Member.model_schema()
    assert schema.name == "Member"
    assert schema.primary_key == "id"
    names = schema.field_names
    assert "team" not in names  # relationship, not a scalar field
    assert "handle" in names


def test_relationship_in_schema() -> None:
    schema = Member.model_schema()
    rels = {r.name: r for r in schema.relationships}
    assert rels["team"].target == "Team"
    assert rels["team"].kind == "link"
    assert rels["team"].on_delete == "cascade"


def test_vector_metadata_in_schema() -> None:
    schema = Member.model_schema()
    emb = schema.field("embedding")
    assert emb is not None
    assert emb.type == "vector"
    assert emb.vector_dim == 16
    assert emb.vector_index == "hnsw"


def test_indexes_include_unique_and_pk() -> None:
    schema = Team.model_schema()
    assert set(schema.indexes) == {"id", "name"}


def test_schema_document_is_deterministic() -> None:
    a = schema_json([Member, Team])
    b = schema_json([Team, Member])
    assert a == b  # order-independent, stable key ordering


def test_schema_document_structure() -> None:
    doc = schema_document([Team])
    assert doc["format_version"] == 1
    assert doc["models"][0]["name"] == "Team"
