"""First-class fixed-dimension vector type.

``Vector[N]`` is a parametrized type usable in model annotations. A :class:`Vector`
runtime value validates its dimension and that all components are finite numbers, and
serializes to a plain ``list[float]`` inside protocol payloads. Large-scale ANN search
is performed by the server; the client only validates and transports vectors and
expresses distance queries.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, ClassVar

from ._native import pack_f32_vector, unpack_f32_vector, validate_vector
from .errors import AuraValidationError

__all__ = ["Vector", "is_vector_type", "vector_dimension"]

_SUBCLASS_CACHE: dict[int, type[Vector]] = {}


class Vector(Sequence[float]):
    """A dense numeric vector with an optional fixed dimension.

    ``Vector[768]`` produces a dimension-bound subclass usable as an annotation and as
    a constructor. A bare ``Vector([...])`` accepts any length unless a ``dim`` is
    supplied.
    """

    dim: ClassVar[int | None] = None

    __slots__ = ("_data",)

    def __init__(self, values: Iterable[float], *, dim: int | None = None) -> None:
        expected = dim if dim is not None else type(self).dim
        if expected is not None:
            # Fixed-dimension path: validation (and any native acceleration) runs
            # through the shared adapter so the behaviour is identical everywhere.
            self._data: list[float] = validate_vector(list(values), expected)
        else:
            self._data = [self._coerce(v) for v in values]

    @staticmethod
    def _coerce(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AuraValidationError(
                f"Vector components must be numeric, got {type(value).__name__}"
            ) from exc
        if not math.isfinite(number):
            raise AuraValidationError("Vector components must be finite numbers")
        return number

    def __class_getitem__(cls, dim: int) -> type[Vector]:
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise AuraValidationError(f"Vector dimension must be a positive int, got {dim!r}")
        cached = _SUBCLASS_CACHE.get(dim)
        if cached is None:
            cached = type(f"Vector{dim}", (cls,), {"dim": dim, "__slots__": ()})
            _SUBCLASS_CACHE[dim] = cached
        return cached

    # -- sequence protocol -------------------------------------------------------
    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: Any) -> Any:
        return self._data[index]

    def __iter__(self) -> Iterator[float]:
        return iter(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Vector):
            return self._data == other._data
        if isinstance(other, (list, tuple)):
            return self._data == [float(x) for x in other]
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(self._data))

    def __repr__(self) -> str:
        dim = type(self).dim
        prefix = f"Vector[{dim}]" if dim is not None else "Vector"
        if len(self._data) > 6:
            preview = ", ".join(f"{x:g}" for x in self._data[:3])
            return f"{prefix}([{preview}, ... ({len(self._data)} dims)])"
        return f"{prefix}({self._data!r})"

    def to_list(self) -> list[float]:
        """Return a plain list copy of the components (used for serialization)."""
        return list(self._data)

    def tolist(self) -> list[float]:
        """NumPy-compatible alias for :meth:`to_list`."""
        return list(self._data)

    @property
    def dimension(self) -> int:
        """The actual number of components in this vector."""
        return len(self._data)

    def magnitude(self) -> float:
        """Euclidean norm of the vector."""
        return math.sqrt(sum(x * x for x in self._data))

    def pack(self) -> bytes:
        """Pack into contiguous little-endian IEEE-754 f32 bytes (``4 * dimension``).

        Uses the optional native backend when available; the pure-Python result is
        byte-for-byte identical. Note that f32 packing narrows from Python's f64, so
        ``Vector.from_bytes(v.pack(), v.dimension)`` round-trips at f32 precision.
        """
        return pack_f32_vector(self._data, len(self._data))

    @classmethod
    def from_bytes(cls, data: bytes, dimension: int) -> Vector:
        """Inverse of :meth:`pack`: decode ``4 * dimension`` little-endian f32 bytes."""
        return cls(unpack_f32_vector(data, dimension), dim=dimension)


def is_vector_type(annotation: Any) -> bool:
    """Return ``True`` if ``annotation`` is :class:`Vector` or a ``Vector[N]`` subclass."""
    return isinstance(annotation, type) and issubclass(annotation, Vector)


def vector_dimension(annotation: Any) -> int | None:
    """Return the fixed dimension of a ``Vector[N]`` annotation, or ``None``."""
    if is_vector_type(annotation):
        dim: int | None = annotation.dim
        return dim
    return None
