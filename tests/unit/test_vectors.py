"""Tests for the vector type."""

from __future__ import annotations

import math

import pytest

from aura.errors import AuraValidationError
from aura.vectors import Vector, is_vector_type, vector_dimension


def test_vector_basic() -> None:
    v = Vector([1.0, 2.0, 3.0])
    assert len(v) == 3
    assert v.dimension == 3
    assert v.to_list() == [1.0, 2.0, 3.0]
    assert list(v) == [1.0, 2.0, 3.0]
    assert v[1] == 2.0


def test_fixed_dimension_subclass() -> None:
    V4 = Vector[4]
    assert V4.dim == 4
    assert is_vector_type(V4)
    assert vector_dimension(V4) == 4
    v = V4([1, 2, 3, 4])
    assert isinstance(v, Vector)


def test_fixed_dimension_is_cached() -> None:
    assert Vector[8] is Vector[8]


def test_dimension_mismatch_raises() -> None:
    with pytest.raises(AuraValidationError):
        Vector[3]([1, 2])


def test_explicit_dim_argument() -> None:
    with pytest.raises(AuraValidationError):
        Vector([1, 2, 3], dim=2)


def test_non_numeric_rejected() -> None:
    with pytest.raises(AuraValidationError):
        Vector(["a", "b"])


def test_non_finite_rejected() -> None:
    with pytest.raises(AuraValidationError):
        Vector([1.0, float("nan")])
    with pytest.raises(AuraValidationError):
        Vector([1.0, float("inf")])


def test_equality() -> None:
    assert Vector([1, 2]) == Vector([1, 2])
    assert Vector([1, 2]) == [1.0, 2.0]
    assert Vector([1, 2]) != Vector([1, 3])


def test_magnitude() -> None:
    assert math.isclose(Vector([3, 4]).magnitude(), 5.0)


def test_invalid_dimension_parameter() -> None:
    with pytest.raises(AuraValidationError):
        Vector[0]
    with pytest.raises(AuraValidationError):
        Vector[-1]


def test_is_vector_type_false_for_other() -> None:
    assert is_vector_type(int) is False
    assert vector_dimension(int) is None


def test_repr_truncates_large_vectors() -> None:
    text = repr(Vector(list(range(100))))
    assert "100 dims" in text
