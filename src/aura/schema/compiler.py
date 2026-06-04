"""Compile model schemas into a deterministic schema document.

The output is stable across runs and processes so it can be committed and diffed in
CI. ``compile_models`` orders models and fields deterministically and
emits a canonical-JSON-ready dict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .model_schema import ModelSchema

if TYPE_CHECKING:
    from ..models import AuraModel

__all__ = ["compile_models", "schema_document", "schema_json"]

SCHEMA_FORMAT_VERSION = 1


def compile_models(models: Iterable[type[AuraModel]]) -> list[ModelSchema]:
    """Return the :class:`ModelSchema` for each model, sorted by model name."""
    schemas = [m.__aura_schema__ for m in models]
    return sorted(schemas, key=lambda s: s.name)


def schema_document(models: Iterable[type[AuraModel]]) -> dict[str, Any]:
    """Build the full, deterministic schema document for a set of models."""
    schemas = compile_models(models)
    return {
        "format_version": SCHEMA_FORMAT_VERSION,
        "models": [s.to_dict() for s in schemas],
    }


def schema_json(models: Iterable[type[AuraModel]]) -> str:
    """Serialize the schema document as canonical (sorted-key) JSON."""
    return json.dumps(schema_document(models), sort_keys=True, separators=(",", ":"))
