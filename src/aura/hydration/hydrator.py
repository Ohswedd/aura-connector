"""Hydration: protocol result rows into model instances.

The hydrator constructs models through :meth:`AuraModel._aura_construct`, which bypasses
the user ``__init__`` and invokes the ``__aura_post_load__`` hook. It
handles scalars, optionals, lists, document/dict fields, nested models, relationship
includes, vectors, partial-selected rows, and raises a clear error when a required
field is missing from a full result.
"""

from __future__ import annotations

from typing import Any

from ..errors import AuraValidationError
from ..fields import FieldInfo
from ..models import AuraModel, coerce_scalar
from ..vectors import Vector

__all__ = ["Hydrator", "hydrate_rows"]


class Hydrator:
    """Builds model instances from result rows."""

    def hydrate_rows(
        self, model: type[AuraModel], rows: list[dict[str, Any]], *, partial: bool = False
    ) -> list[AuraModel]:
        return [self.hydrate_row(model, row, partial=partial) for row in rows]

    def hydrate_row(
        self, model: type[AuraModel], row: dict[str, Any], *, partial: bool = False
    ) -> AuraModel:
        fields = model.__aura_fields__
        values: dict[str, Any] = {}
        loaded: set[str] = set()

        for name, info in fields.items():
            key = info.storage_name
            present = key in row
            if not present:
                if info.is_relationship:
                    continue
                if partial:
                    continue
                if info.has_default:
                    values[name] = info.build_default()
                    continue
                if info.nullable:
                    values[name] = None
                    continue
                raise AuraValidationError(
                    f"Missing required field {model.__name__}.{name} in result row",
                    context={"model": model.__name__, "field": name},
                )

            raw = row[key]
            if info.is_relationship and not self._is_loaded_shape(info, raw):
                # A bare foreign-key reference (not an included nested object) means the
                # relationship was not loaded; leave it inaccessible rather than faking it.
                continue
            values[name] = self._hydrate_value(info, raw, partial=partial)
            if info.is_relationship:
                loaded.add(name)

        instance = model._aura_construct(values, loaded)
        score = row.get("__score__")
        if score is not None:
            instance.__dict__["__score__"] = float(score)
        # Hybrid / ranked component scores and the 1-based rank, when the server
        # (or reference engine) reported them.
        for raw_key, attr in (
            ("__text_score__", "__text_score__"),
            ("__vector_score__", "__vector_score__"),
        ):
            value = row.get(raw_key)
            if value is not None:
                instance.__dict__[attr] = float(value)
        rank = row.get("__rank__")
        if rank is not None:
            instance.__dict__["__rank__"] = int(rank)
        return instance

    @staticmethod
    def _is_loaded_shape(info: FieldInfo, raw: Any) -> bool:
        """Whether ``raw`` is an included nested object (vs. a bare FK reference)."""
        if raw is None:
            return True  # an explicitly null link is a loaded value
        if info.container == "list":
            return isinstance(raw, list)
        return isinstance(raw, dict)

    def _hydrate_value(self, info: FieldInfo, raw: Any, *, partial: bool) -> Any:
        if raw is None:
            return None
        if info.is_vector:
            return Vector(raw, dim=info.vector_dim)
        if info.is_relationship:
            target = info.model_type
            if not (isinstance(target, type) and issubclass(target, AuraModel)):
                return raw
            if info.container == "list":
                return [self.hydrate_row(target, item, partial=partial) for item in raw]
            return self.hydrate_row(target, raw, partial=partial)
        if info.container == "list":
            element = info.model_type
            return [coerce_scalar(element, item) for item in raw]
        if info.container == "dict":
            return dict(raw)
        return coerce_scalar(info.python_type if info.model_type is None else info.model_type, raw)


_DEFAULT_HYDRATOR = Hydrator()


def hydrate_rows(
    model: type[AuraModel], rows: list[dict[str, Any]], *, partial: bool = False
) -> list[AuraModel]:
    """Hydrate ``rows`` into instances of ``model`` using the default hydrator."""
    return _DEFAULT_HYDRATOR.hydrate_rows(model, rows, partial=partial)
