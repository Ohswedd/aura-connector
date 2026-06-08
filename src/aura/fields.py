"""Declarative field definitions.

``Field(...)`` returns a :class:`FieldInfo` marker that the model metaclass reads to
build a model's schema. Invalid field configurations raise :class:`AuraSchemaError`
at class-definition time so mistakes surface immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from .errors import AuraSchemaError

__all__ = ["MISSING", "Field", "FieldInfo"]


class _Missing:
    """Sentinel for "no value provided" — distinct from ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


@dataclass
class FieldInfo:
    """Resolved metadata for a single model field.

    Most attributes are populated by :func:`Field`; ``name``, ``python_type`` and the
    derived type flags are filled in by the model metaclass during class creation.
    """

    primary_key: bool = False
    unique: bool = False
    index: bool = False
    full_text: bool = False
    nullable: bool = False
    default: Any = MISSING
    default_factory: Callable[[], Any] | None = None
    alias: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = dc_field(default_factory=dict)
    link: bool = False
    on_delete: str | None = None
    vector_index: str | None = None

    # -- populated by the metaclass ---------------------------------------------
    name: str = ""
    python_type: Any = None
    is_optional: bool = False
    container: str = "none"  # one of: none, list, dict
    is_vector: bool = False
    vector_dim: int | None = None
    is_model: bool = False
    model_type: Any = None
    is_relationship: bool = False

    def __post_init__(self) -> None:
        if self.default is not MISSING and self.default_factory is not None:
            raise AuraSchemaError("Field cannot set both 'default' and 'default_factory'")
        if self.primary_key and self.nullable:
            raise AuraSchemaError("A primary_key field cannot be nullable")
        if self.on_delete is not None and self.on_delete not in {"cascade", "restrict", "set_null"}:
            raise AuraSchemaError(
                f"Invalid on_delete={self.on_delete!r}; expected cascade|restrict|set_null"
            )

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING or self.default_factory is not None

    @property
    def storage_name(self) -> str:
        """Name used in serialized payloads (alias if provided, else field name)."""
        return self.alias or self.name

    def build_default(self) -> Any:
        """Produce a fresh default value, or :data:`MISSING` if none is configured."""
        if self.default_factory is not None:
            return self.default_factory()
        if self.default is not MISSING:
            return self.default
        return MISSING

    def is_required(self) -> bool:
        """Whether a value must be supplied on construction."""
        return not self.has_default and not self.nullable and not self.is_relationship


def Field(
    *,
    primary_key: bool = False,
    unique: bool = False,
    index: bool = False,
    full_text: bool = False,
    nullable: bool = False,
    default: Any = MISSING,
    default_factory: Callable[[], Any] | None = None,
    alias: str | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    link: bool = False,
    on_delete: str | None = None,
    vector_index: str | None = None,
) -> Any:
    """Declare a model field. Returns a :class:`FieldInfo` typed as ``Any``.

    The ``Any`` return lets a field annotated as ``int`` accept ``Field(...)`` as its
    assigned default without upsetting static type checkers, mirroring the ergonomics
    of Pydantic and dataclasses.
    """
    return FieldInfo(
        primary_key=primary_key,
        unique=unique,
        index=index,
        full_text=full_text,
        nullable=nullable,
        default=default,
        default_factory=default_factory,
        alias=alias,
        description=description,
        metadata=dict(metadata or {}),
        link=link,
        on_delete=on_delete,
        vector_index=vector_index,
    )
