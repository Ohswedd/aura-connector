"""Unit tests for the backend capability model."""

from __future__ import annotations

import pytest

from aura.backends.capabilities import CAPABILITY_FLAGS, BackendCapabilities


def test_capabilities_default_to_false() -> None:
    caps = BackendCapabilities(name="x")
    for flag in CAPABILITY_FLAGS:
        assert caps.supports(flag) is False


def test_supports_reads_each_flag() -> None:
    caps = BackendCapabilities(name="x", transactions=True, vector_search=False)
    assert caps.supports("transactions") is True
    assert caps.supports("vector_search") is False


def test_supports_unknown_flag_raises() -> None:
    caps = BackendCapabilities(name="x")
    with pytest.raises(ValueError):
        caps.supports("teleportation")


def test_to_dict_includes_name_and_all_flags() -> None:
    caps = BackendCapabilities(name="sqlite", transactions=True)
    data = caps.to_dict()
    assert data["name"] == "sqlite"
    assert data["transactions"] is True
    for flag in CAPABILITY_FLAGS:
        assert flag in data


def test_capability_flags_match_dataclass_fields() -> None:
    caps = BackendCapabilities(name="x")
    data = caps.to_dict()
    assert set(CAPABILITY_FLAGS) == set(data) - {"name"}
