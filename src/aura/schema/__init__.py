"""Schema representation and compilation for Aura models."""

from __future__ import annotations

from .compiler import (
    SCHEMA_FORMAT_VERSION,
    compile_models,
    schema_document,
    schema_json,
)
from .migrations import (
    Change,
    LockImpactEstimate,
    MigrationPlan,
    diff_schemas,
    generate_migration,
)
from .model_schema import FieldSchema, ModelSchema, RelationshipSchema

__all__ = [
    "SCHEMA_FORMAT_VERSION",
    "Change",
    "FieldSchema",
    "LockImpactEstimate",
    "MigrationPlan",
    "ModelSchema",
    "RelationshipSchema",
    "compile_models",
    "diff_schemas",
    "generate_migration",
    "schema_document",
    "schema_json",
]
