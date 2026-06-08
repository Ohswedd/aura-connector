"""Declarative model system.

A model is a plain Python class deriving from :class:`AuraModel`. The
:class:`AuraModelMeta` metaclass compiles annotations and :func:`~aura.fields.Field`
markers into an immutable :class:`~aura.schema.ModelSchema`, installs a typed
descriptor for every field (so ``User.id`` is a query expression and ``user.id`` is a
value), and validates construction.

The model layer is backend-independent: it knows nothing about transports, protocol
frames, or network state.
"""

from __future__ import annotations

import base64
import datetime
import decimal
import enum
import uuid
from collections.abc import Mapping
from typing import Any, ClassVar, ForwardRef, Union, get_args, get_origin, get_type_hints

from .errors import AuraSchemaError, AuraValidationError, RelationshipNotLoadedError
from .fields import MISSING, FieldInfo
from .query.expressions import FieldReference
from .schema.model_schema import FieldSchema, ModelSchema, RelationshipSchema
from .vectors import Vector, is_vector_type, vector_dimension

__all__ = ["AuraModel", "AuraModelMeta", "get_model", "registered_models"]

_MODEL_REGISTRY: dict[str, type[AuraModel]] = {}
_NONE_TYPE = type(None)


def get_model(name: str) -> type[AuraModel] | None:
    """Return a registered model class by name, or ``None``."""
    return _MODEL_REGISTRY.get(name)


def registered_models() -> dict[str, type[AuraModel]]:
    """Return a copy of the global model registry."""
    return dict(_MODEL_REGISTRY)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """If ``annotation`` is ``Optional[X]``/``X | None`` return ``(X, True)``."""
    origin = get_origin(annotation)
    if origin is Union or origin is getattr(__import__("types"), "UnionType", object()):
        args = [a for a in get_args(annotation) if a is not _NONE_TYPE]
        has_none = len(args) != len(get_args(annotation))
        if len(args) == 1:
            return args[0], has_none
        # A genuine multi-type union; treat as opaque scalar.
        return annotation, has_none
    return annotation, False


def _analyze(annotation: Any, field_info: FieldInfo) -> None:
    """Populate the derived type flags on ``field_info`` from ``annotation``."""
    inner, optional = _unwrap_optional(annotation)
    field_info.is_optional = optional
    if optional:
        field_info.nullable = True

    origin = get_origin(inner)
    if origin in (list, set, tuple, frozenset):
        field_info.container = "list"
        element = get_args(inner)[0] if get_args(inner) else Any
        _classify_element(field_info, element)
        field_info.python_type = inner
        return
    if origin in (dict, Mapping):
        field_info.container = "dict"
        field_info.python_type = inner
        return

    _classify_element(field_info, inner)
    field_info.python_type = inner


def _classify_element(field_info: FieldInfo, element: Any) -> None:
    if is_vector_type(element):
        field_info.is_vector = True
        field_info.vector_dim = vector_dimension(element)
        return
    if isinstance(element, type) and issubclass(element, AuraModel):
        field_info.is_model = True
        field_info.model_type = element
        field_info.is_relationship = True
        return
    field_info.model_type = element


def _to_jsonable(value: Any) -> Any:
    """Convert a Python value into a JSON-serializable payload value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Vector):
        return value.to_list()
    if isinstance(value, AuraModel):
        return value.to_dict()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, enum.Enum):
        return _to_jsonable(value.value)
    raise AuraValidationError(
        f"Cannot serialize value of type {type(value).__name__}",
        context={"type": type(value).__name__},
    )


def coerce_scalar(target: Any, value: Any) -> Any:
    """Coerce a JSON-decoded ``value`` into the model's declared scalar type."""
    if value is None or target is None or target is Any:
        return value
    if isinstance(target, type):
        if issubclass(target, bool):
            return bool(value)
        if issubclass(target, uuid.UUID) and isinstance(value, str):
            return uuid.UUID(value)
        if issubclass(target, datetime.datetime) and isinstance(value, str):
            return datetime.datetime.fromisoformat(value)
        if (
            issubclass(target, datetime.date)
            and not issubclass(target, datetime.datetime)
            and isinstance(value, str)
        ):
            return datetime.date.fromisoformat(value)
        if issubclass(target, decimal.Decimal) and isinstance(value, (str, int, float)):
            return decimal.Decimal(str(value))
        if issubclass(target, bytes) and isinstance(value, Mapping) and "__bytes__" in value:
            return base64.b64decode(value["__bytes__"])
        if issubclass(target, enum.Enum):
            return target(value)
        if issubclass(target, (int, float, str)) and not isinstance(value, target):
            return target(value)
    return value


class FieldDescriptor:
    """Data descriptor giving class-level expressions and instance-level values."""

    __slots__ = ("info", "name")

    def __init__(self, info: FieldInfo) -> None:
        self.info = info
        self.name = info.name

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return FieldReference(
                model=owner.__name__ if owner is not None else "",
                name=self.name,
                vector_dim=self.info.vector_dim,
            )
        values: dict[str, Any] = instance.__dict__["__aura_values__"]
        if self.info.is_relationship:
            loaded: set[str] = instance.__dict__["__aura_loaded__"]
            if self.name not in loaded:
                raise RelationshipNotLoadedError(
                    f"Relationship {owner.__name__ if owner else ''}.{self.name} was not "
                    "loaded; include it explicitly with .include() to access it",
                    context={"relationship": self.name},
                )
        if self.name not in values:
            raise AttributeError(
                f"{owner.__name__ if owner else ''}.{self.name} is not available; it was "
                "not selected in this query's projection"
            )
        return values[self.name]

    def __set__(self, instance: Any, value: Any) -> None:
        instance.__dict__["__aura_values__"][self.name] = value
        if self.info.is_relationship:
            instance.__dict__["__aura_loaded__"].add(self.name)


class AuraModelMeta(type):
    """Metaclass that compiles a model class into fields, descriptors, and a schema."""

    __aura_fields__: dict[str, FieldInfo]
    __aura_schema__: ModelSchema

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> AuraModelMeta:
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        model_bases = [b for b in bases if isinstance(b, AuraModelMeta)]
        if not model_bases:
            # This is the AuraModel base class itself.
            cls.__aura_fields__ = {}
            return cls

        fields: dict[str, FieldInfo] = {}
        for base in model_bases:
            fields.update(base.__aura_fields__)

        own_annotations = namespace.get("__annotations__", {})
        resolved = _resolve_hints(cls)

        for attr_name, raw_annotation in own_annotations.items():
            if attr_name.startswith("__"):
                continue
            annotation = resolved.get(attr_name, raw_annotation)
            if get_origin(annotation) is ClassVar:
                continue

            default = namespace.get(attr_name, MISSING)
            if isinstance(default, FieldInfo):
                info = default
            else:
                info = FieldInfo(default=default if default is not MISSING else MISSING)
            info.name = attr_name
            _analyze(annotation, info)
            if info.link and not info.is_relationship:
                raise AuraSchemaError(
                    f"{name}.{attr_name}: link=True requires a model-typed annotation"
                )
            fields[attr_name] = info
            setattr(cls, attr_name, FieldDescriptor(info))

        cls.__aura_fields__ = fields
        cls.__aura_schema__ = _build_schema(name, fields)

        primary_keys = [f for f in fields.values() if f.primary_key]
        if len(primary_keys) > 1:
            raise AuraSchemaError(f"{name} declares multiple primary keys")

        _MODEL_REGISTRY[name] = cls  # type: ignore[assignment]
        return cls


def _resolve_hints(cls: type) -> dict[str, Any]:
    # Pass only localns so get_type_hints resolves each base's annotations against
    # that base's own module globals (the AuraModel base needs its own imports such
    # as ClassVar). The registry is supplied as locals to resolve forward references.
    localns = dict(_MODEL_REGISTRY)
    try:
        return get_type_hints(cls, localns=localns)
    except NameError as exc:
        raise AuraSchemaError(
            f"{cls.__name__}: could not resolve a type annotation ({exc}). Define "
            "referenced models before the models that reference them, or use a string "
            "annotation for an already-registered model.",
            context={"model": cls.__name__},
        ) from exc


def _build_schema(name: str, fields: Mapping[str, FieldInfo]) -> ModelSchema:
    field_schemas: list[FieldSchema] = []
    relationships: list[RelationshipSchema] = []
    primary_key: str | None = None

    for info in fields.values():
        if info.is_relationship:
            target = info.model_type
            target_name = (
                target.__name__
                if isinstance(target, type)
                else getattr(target, "__forward_arg__", str(target))
            )
            relationships.append(
                RelationshipSchema(
                    name=info.name,
                    target=target_name,
                    kind="collection" if info.container == "list" else "link",
                    on_delete=info.on_delete,
                )
            )
            continue

        if info.primary_key:
            primary_key = info.name

        type_label = _schema_type_label(info)
        field_schemas.append(
            FieldSchema(
                name=info.name,
                type=type_label,
                nullable=info.nullable,
                primary_key=info.primary_key,
                unique=info.unique,
                index=info.index,
                full_text=info.full_text,
                container=info.container,
                vector_dim=info.vector_dim,
                vector_index=info.vector_index,
                has_default=info.has_default,
                description=info.description,
                alias=info.alias,
            )
        )

    return ModelSchema(
        name=name,
        fields=tuple(field_schemas),
        relationships=tuple(relationships),
        primary_key=primary_key,
    )


def _schema_type_label(info: FieldInfo) -> str:
    if info.is_vector:
        return "vector"
    if info.container == "list":
        return "list"
    if info.container == "dict":
        return "document"
    target = info.model_type
    if isinstance(target, type):
        return target.__name__
    if isinstance(target, (str, ForwardRef)):
        return getattr(target, "__forward_arg__", str(target))
    return "any"


class AuraModel(metaclass=AuraModelMeta):
    """Base class for all Aura models.

    Subclasses declare fields with type annotations. Construction validates required
    fields and coerces vectors and nested models. Database hydration uses a separate
    construction path (:meth:`_aura_construct`) that bypasses ``__init__`` and invokes
    the optional :meth:`__aura_post_load__` hook.
    """

    __aura_fields__: ClassVar[dict[str, FieldInfo]] = {}
    __aura_schema__: ClassVar[ModelSchema]

    def __init__(self, **data: Any) -> None:
        fields = type(self).__aura_fields__
        alias_map = {f.alias: name for name, f in fields.items() if f.alias}
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            target = key if key in fields else alias_map.get(key)
            if target is None:
                raise AuraValidationError(
                    f"{type(self).__name__} has no field {key!r}",
                    context={"field": key},
                )
            normalized[target] = value

        values: dict[str, Any] = {}
        loaded: set[str] = set()
        for fname, info in fields.items():
            if fname in normalized:
                coerced = self._coerce_input(info, normalized[fname])
                values[fname] = coerced
                if info.is_relationship:
                    loaded.add(fname)
                continue
            if info.has_default:
                values[fname] = self._coerce_input(info, info.build_default())
            elif info.is_relationship:
                continue  # not loaded; access raises RelationshipNotLoadedError
            elif info.nullable:
                values[fname] = None
            else:
                raise AuraValidationError(
                    f"Missing required field {type(self).__name__}.{fname}",
                    context={"field": fname},
                )

        self.__dict__["__aura_values__"] = values
        self.__dict__["__aura_loaded__"] = loaded

    @staticmethod
    def _coerce_input(info: FieldInfo, value: Any) -> Any:
        if value is None:
            return None
        if info.is_vector:
            if isinstance(value, Vector):
                if info.vector_dim is not None and value.dimension != info.vector_dim:
                    raise AuraValidationError(
                        f"Vector field {info.name!r} expects dimension {info.vector_dim}, "
                        f"got {value.dimension}",
                        context={"field": info.name},
                    )
                return value
            return Vector(value, dim=info.vector_dim)
        if info.is_relationship:
            target = info.model_type
            if info.container == "list":
                return [_coerce_model(target, item) for item in value]
            return _coerce_model(target, value)
        if info.container == "list":
            return list(value)
        if info.container == "dict":
            return dict(value)
        return value

    # -- hydration construction path --------------------------------------------
    @classmethod
    def _aura_construct(cls, values: dict[str, Any], loaded_relationships: set[str]) -> AuraModel:
        """Build an instance from already-coerced values, bypassing ``__init__``."""
        instance = cls.__new__(cls)
        instance.__dict__["__aura_values__"] = values
        instance.__dict__["__aura_loaded__"] = set(loaded_relationships)
        instance.__aura_post_load__()
        return instance

    def __aura_post_load__(self) -> None:
        """Hook invoked after database hydration. Override to post-process."""

    # -- serialization -----------------------------------------------------------
    def to_dict(self, *, include_unloaded: bool = False) -> dict[str, Any]:
        """Serialize to a JSON-able dict keyed by storage name (alias-aware)."""
        fields = type(self).__aura_fields__
        values: dict[str, Any] = self.__dict__["__aura_values__"]
        loaded: set[str] = self.__dict__["__aura_loaded__"]
        out: dict[str, Any] = {}
        for fname, info in fields.items():
            if info.is_relationship and fname not in loaded:
                if include_unloaded:
                    continue
                continue
            if fname not in values:
                continue
            out[info.storage_name] = _to_jsonable(values[fname])
        return out

    def primary_key_value(self) -> Any:
        schema = type(self).__aura_schema__
        if schema.primary_key is None:
            return None
        return self.__dict__["__aura_values__"].get(schema.primary_key)

    @classmethod
    def model_schema(cls) -> ModelSchema:
        return cls.__aura_schema__

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        other_values = other.__dict__["__aura_values__"]
        return bool(self.__dict__["__aura_values__"] == other_values)

    def __hash__(self) -> int:
        pk = self.primary_key_value()
        if pk is None:
            return object.__hash__(self)
        return hash((type(self).__name__, pk))

    def __repr__(self) -> str:
        values: dict[str, Any] = self.__dict__["__aura_values__"]
        parts = ", ".join(f"{k}={v!r}" for k, v in values.items())
        return f"{type(self).__name__}({parts})"


def _coerce_model(target: Any, value: Any) -> Any:
    if isinstance(value, AuraModel):
        return value
    if isinstance(value, Mapping) and isinstance(target, type):
        return target(**value)
    if isinstance(target, type) and issubclass(target, AuraModel):
        raise AuraValidationError(
            f"Expected {target.__name__} instance or mapping, got {type(value).__name__}"
        )
    return value
