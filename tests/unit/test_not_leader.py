"""Unit tests for AuraDB cluster-preview ``not_leader`` error mapping.

These exercise the connector's error-mapping seam directly: a server error
payload with ``code == "not_leader"`` maps to the dedicated
:class:`aura.AuraNotLeaderError`, which exposes the leader-routing hints the
server provided. Generic (unknown) errors still fall back to the generic
mapping, so non-cluster behavior is preserved.
"""

from __future__ import annotations

import pytest

from aura import AuraNotLeaderError, AuraServerError
from aura.backends.auradb import error_from_frame
from aura.backends.auradb_native import _error_from_payload
from aura.protocol.frames import Frame
from aura.protocol.messages import ErrorBody, encode_body
from aura.protocol.opcodes import Opcode


def _native(payload: dict) -> Exception:
    """Map a decoded native-backend wire error payload to an exception."""
    return _error_from_payload(payload)


def _frame(code: str, message: str, *, retryable: bool = False, context: dict | None = None):
    body = ErrorBody(code=code, message=message, retryable=retryable, context=context or {})
    return Frame(opcode=Opcode.ERROR, payload=encode_body(body.to_payload()), request_id=7)


def test_not_leader_maps_to_dedicated_error() -> None:
    err = _native(
        {
            "code": "not_leader",
            "message": "this node is not the leader",
            "retryable": True,
            "not_leader": {"leader_client_addr": "10.0.0.2:7171"},
        }
    )
    assert isinstance(err, AuraNotLeaderError)
    assert err.code == "not_leader"
    assert err.message == "this node is not the leader"
    # The reference-path mapping agrees with the native path.
    ref = error_from_frame(
        _frame(
            "not_leader", "nope", context={"not_leader": {"leader_client_addr": "10.0.0.2:7171"}}
        )
    )
    assert isinstance(ref, AuraNotLeaderError)
    assert ref.leader_addr == "10.0.0.2:7171"


def test_not_leader_missing_hint_still_safe() -> None:
    err = _native({"code": "not_leader", "message": "no leader yet", "retryable": True})
    assert isinstance(err, AuraNotLeaderError)
    assert err.leader_addr is None
    assert err.leader_client_addr is None
    assert err.leader_node_id is None
    assert err.current_node_id is None
    # str() must not raise even with no hint.
    assert "not_leader" in str(err)


def test_not_leader_extracts_leader_client_addr() -> None:
    # Nested under ``not_leader`` …
    nested = _native(
        {
            "code": "not_leader",
            "message": "m",
            "not_leader": {"leader_client_addr": "127.0.0.1:7373"},
        }
    )
    assert isinstance(nested, AuraNotLeaderError)
    assert nested.leader_client_addr == "127.0.0.1:7373"
    assert nested.leader_addr == "127.0.0.1:7373"
    # … or top-level, both work.
    top = _native({"code": "not_leader", "message": "m", "leader_client_addr": "127.0.0.1:7474"})
    assert isinstance(top, AuraNotLeaderError)
    assert top.leader_addr == "127.0.0.1:7474"


def test_not_leader_extracts_leader_node_id() -> None:
    err = _native(
        {
            "code": "not_leader",
            "message": "m",
            "not_leader": {
                "leader_node_id": "00000000000000aa",
                "current_node_id": "0000000000000001",
            },
        }
    )
    assert isinstance(err, AuraNotLeaderError)
    assert err.leader_node_id == "00000000000000aa"
    assert err.current_node_id == "0000000000000001"
    # ``leader_id`` is accepted as an alias for the leader node id.
    alias = _native({"code": "not_leader", "message": "m", "leader_id": "00000000000000bb"})
    assert isinstance(alias, AuraNotLeaderError)
    assert alias.leader_node_id == "00000000000000bb"


def test_not_leader_retryable_flag() -> None:
    known = _native(
        {"code": "not_leader", "message": "m", "retryable": True, "leader_client_addr": "h:1"}
    )
    assert known.retryable is True
    not_retryable = _native({"code": "not_leader", "message": "m", "retryable": False})
    assert not_retryable.retryable is False
    # When the server omits the flag, default to retryable (a leader may exist).
    default = _native({"code": "not_leader", "message": "m"})
    assert default.retryable is True


def test_not_leader_str_includes_leader_hint() -> None:
    err = _native(
        {
            "code": "not_leader",
            "message": "m",
            "not_leader": {"leader_client_addr": "10.1.2.3:7171"},
        }
    )
    assert "10.1.2.3:7171" in str(err)
    # With only a node id known, str() names the node and says the address is unknown.
    node_only = _native(
        {"code": "not_leader", "message": "m", "not_leader": {"leader_node_id": "00000000000000aa"}}
    )
    text = str(node_only)
    assert "00000000000000aa" in text
    assert "unknown" in text


def test_unknown_error_still_maps_generically() -> None:
    # An unknown native code falls back to AuraServerError, not AuraNotLeaderError.
    err = _native({"code": "totally_unknown", "message": "boom"})
    assert isinstance(err, AuraServerError)
    assert not isinstance(err, AuraNotLeaderError)
    # A known non-cluster code is unaffected by the not_leader special case.
    not_found = _native({"code": "not_found", "message": "missing"})
    assert type(not_found).__name__ == "AuraNotFoundError"


def test_not_leader_raw_payload_preserved() -> None:
    payload = {
        "code": "not_leader",
        "message": "m",
        "retryable": True,
        "term": 5,
        "role": "follower",
        "not_leader": {"leader_client_addr": "h:7171", "term": 5, "role": "follower"},
    }
    err = _native(payload)
    assert isinstance(err, AuraNotLeaderError)
    assert err.raw_payload is not None
    assert err.raw_payload.get("term") == 5
    assert err.raw_payload.get("role") == "follower"


@pytest.mark.parametrize("code", ["not_found", "conflict", "unauthenticated", "internal"])
def test_non_cluster_codes_never_become_not_leader(code: str) -> None:
    err = _native({"code": code, "message": "x"})
    assert not isinstance(err, AuraNotLeaderError)


# --------------------------------------------------------------------------- #
# A2 — AuraNotLeaderError.__str__ guidance                                    #
# --------------------------------------------------------------------------- #
def test_not_leader_str_with_leader_addr() -> None:
    err = _native(
        {
            "code": "not_leader",
            "message": "this node is not the leader",
            "retryable": True,
            "not_leader": {
                "leader_client_addr": "10.0.0.2:7171",
                "leader_node_id": "00000000000000aa",
                "current_node_id": "0000000000000001",
            },
        }
    )
    text = str(err)
    # Names the node reached, the leader address, and how to redirect.
    assert "not_leader" in text
    assert "0000000000000001" in text  # current (non-leader) node
    assert "10.0.0.2:7171" in text  # leader address
    assert "connect_to_leader" in text  # actionable redirect hint
    # Never implies an automatic write retry.
    assert "not retried automatically" in text


def test_not_leader_str_without_leader_addr() -> None:
    err = _native({"code": "not_leader", "message": "no leader yet", "retryable": True})
    text = str(err)
    assert "not_leader" in text
    assert "leader unknown" in text
    # With no usable address, point at out-of-band leader discovery, not a retry.
    assert "auradb cluster leader" in text
    assert "connect_to_leader" not in text


def test_not_leader_str_no_secrets() -> None:
    # Even if a token-like value somehow rode along in the payload context, it must
    # never appear in the user-facing string (str only renders ids and host:port).
    err = AuraNotLeaderError(
        "not the leader",
        leader_addr="10.0.0.5:7171",
        current_node_id="0000000000000001",
        context={"authorization": "Bearer super-secret-token", "token": "super-secret-token"},
        request_id=11,
    )
    text = str(err)
    assert "super-secret-token" not in text
    assert "Bearer" not in text
    assert "10.0.0.5:7171" in text  # the safe routing hint is still present


def test_not_leader_retryable_false_message() -> None:
    # retryable=False without a leader address must not imply a safe automatic retry.
    err = _native({"code": "not_leader", "message": "stepping down", "retryable": False})
    assert err.retryable is False
    text = str(err)
    assert "not retried automatically" not in text or err.leader_addr is None
    assert "leader unknown" in text
