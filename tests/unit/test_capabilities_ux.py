"""Unit tests for the capability UX helpers (``require`` / ``describe``)."""

from __future__ import annotations

import pytest

from aura.backends.capabilities import CAPABILITY_FLAGS, BackendCapabilities
from aura.errors import AuraBackendCapabilityError


def _auradb_caps() -> BackendCapabilities:
    return BackendCapabilities(
        name="auradb",
        transactions=True,
        schema_create=True,
        vector_search=True,
        hybrid_search=True,
        full_text_search=True,
        explain=True,
        group_by=True,
        query_profile=True,
        hnsw_preview=True,
    )


def _redis_like_caps() -> BackendCapabilities:
    # A key/value backend that does not advertise AuraDB-specific analytics.
    return BackendCapabilities(name="redis", key_value=True)


def test_capabilities_supports() -> None:
    caps = _auradb_caps()
    assert caps.supports("query_profile") is True
    assert caps.supports("cursor_resume") is False
    # An unknown flag name is a programmer error, not a missing capability.
    with pytest.raises(ValueError):
        caps.supports("teleportation")


def test_capabilities_require_success() -> None:
    caps = _auradb_caps()
    # No exception for a supported capability.
    caps.require("group_by")
    caps.require("hybrid_search")


def test_capabilities_require_missing_raises() -> None:
    caps = _auradb_caps()
    with pytest.raises(AuraBackendCapabilityError) as excinfo:
        caps.require("cursor_resume")
    err = excinfo.value
    assert err.context["backend"] == "auradb"
    assert err.context["capability"] == "cursor_resume"
    # Unknown capability names still raise ValueError via supports().
    with pytest.raises(ValueError):
        caps.require("teleportation")


def test_capabilities_describe() -> None:
    caps = _auradb_caps()
    described = caps.describe()
    assert described["name"] == "auradb"
    assert "query_profile" in described["supported"]
    assert "cursor_resume" in described["unsupported"]
    # supported + unsupported partition the full flag set with no overlap.
    assert set(described["supported"]).isdisjoint(described["unsupported"])
    assert set(described["supported"]) | set(described["unsupported"]) == set(CAPABILITY_FLAGS)


def test_capabilities_backend_specific() -> None:
    # A non-AuraDB backend does not claim AuraDB-specific analytics features.
    redis = _redis_like_caps()
    assert redis.supports("key_value") is True
    assert redis.supports("query_profile") is False
    assert redis.supports("group_by") is False
    with pytest.raises(AuraBackendCapabilityError):
        redis.require("hybrid_search")
