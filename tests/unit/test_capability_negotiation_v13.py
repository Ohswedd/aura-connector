"""v0.7.0 capability negotiation for the AuraDB v1.3.0 additive features.

The four new flags (``group_by``, ``query_profile``, ``hnsw_preview``,
``cursor_resume``) are part of the capability matrix, default to ``False``, and are
advertised by the AuraDB native and in-memory reference backends. The native
backend gates them on what a connected server advertises.
"""

from __future__ import annotations

from aura.backends.auradb import _AURADB_CAPABILITIES
from aura.backends.auradb_native import AuraDBNativeBackend
from aura.backends.capabilities import CAPABILITY_FLAGS, BackendCapabilities
from aura.backends.memory import _MEMORY_CAPABILITIES
from aura.config import parse_dsn
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge

_NEW_FLAGS = ("group_by", "query_profile", "hnsw_preview", "cursor_resume")


def test_new_flags_in_capability_matrix() -> None:
    for flag in _NEW_FLAGS:
        assert flag in CAPABILITY_FLAGS


def test_new_flags_default_false() -> None:
    caps = BackendCapabilities(name="sql")
    for flag in _NEW_FLAGS:
        assert caps.supports(flag) is False


def test_to_dict_round_trips_new_flags() -> None:
    data = BackendCapabilities(name="x", group_by=True).to_dict()
    for flag in _NEW_FLAGS:
        assert flag in data
    assert data["group_by"] is True


def test_auradb_and_memory_advertise_new_flags() -> None:
    for caps in (_AURADB_CAPABILITIES, _MEMORY_CAPABILITIES):
        for flag in _NEW_FLAGS:
            assert caps.supports(flag) is True


def _native_backend() -> AuraDBNativeBackend:
    config = parse_dsn("auradb://localhost:7171/db")
    return AuraDBNativeBackend(
        config, Metrics(), _TelemetryBridge(TelemetryConfig.from_value(None))
    )


def test_native_optimistic_defaults_when_offline() -> None:
    # Before a handshake the native backend reports the optimistic native defaults.
    caps = _native_backend().capabilities()
    for flag in _NEW_FLAGS:
        assert caps.supports(flag) is True


def test_native_gates_on_advertised_server_capabilities() -> None:
    backend = _native_backend()
    # A modern server advertising every v1.3.0 capability.
    backend._server_caps = {
        "full_text_bm25_ranking",
        "hybrid_search",
        "vector_exact_search",
        "group_by_aggregation",
        "query_profile",
        "approximate_vector_search",
        "ranked_search_cursor",
    }
    caps = backend.capabilities()
    for flag in _NEW_FLAGS:
        assert caps.supports(flag) is True


def test_native_reports_false_for_older_server() -> None:
    backend = _native_backend()
    # An older server that predates the v1.3.0 features advertises none of them.
    backend._server_caps = {
        "full_text_bm25_ranking",
        "hybrid_search",
        "vector_exact_search",
    }
    caps = backend.capabilities()
    for flag in _NEW_FLAGS:
        assert caps.supports(flag) is False
    # The pre-existing search features remain advertised.
    assert caps.supports("full_text_search") is True
