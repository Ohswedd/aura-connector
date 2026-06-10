"""Unit tests for :class:`aura.ConnectionProfile` and ``from_profile`` wiring."""

from __future__ import annotations

import pytest

from aura import Aura, ConnectionProfile, TokenAuth
from aura.errors import AuraValidationError


def test_connection_profile_from_env_minimal() -> None:
    profile = ConnectionProfile.from_env(environ={"AURA_ADDR": "aura://db.internal:7000/app"})
    assert profile.addr == "aura://db.internal:7000/app"
    assert profile.auth_token is None
    assert profile.tls_ca is None
    # Defaults are safe and explicit.
    assert profile.connect_timeout_ms == 10_000
    assert profile.request_timeout_ms == 30_000
    assert profile.isolation == "snapshot"


def test_connection_profile_from_env_auth_token_redacted() -> None:
    profile = ConnectionProfile.from_env(
        environ={"AURA_ADDR": "aura://h:7000/app", "AURA_AUTH_TOKEN": "super-secret"}
    )
    assert profile.auth_token == "super-secret"
    # The token resolves into a redacted TokenAuth on the built config.
    config = profile.to_client_config()
    assert isinstance(config.auth, TokenAuth)
    assert config.auth.token == "super-secret"
    assert "super-secret" not in repr(config)


def test_connection_profile_from_env_tls(tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nnot-a-real-cert\n-----END CERTIFICATE-----\n")
    profile = ConnectionProfile.from_env(
        environ={
            "AURA_ADDR": "auras://h:7000/app",
            "AURA_TLS_CA": str(ca),
            "AURA_SERVER_NAME": "db.internal",
        }
    )
    assert profile.tls_ca == str(ca)
    assert profile.server_name == "db.internal"
    config = profile.to_client_config()
    assert config.tls.enabled is True
    assert config.tls.ca_cert_path == str(ca)
    # The SNI override is carried as the transport option the TCP transport reads.
    assert config.options["tls_server_name"] == "db.internal"


def test_connection_profile_tls_ca_must_exist() -> None:
    with pytest.raises(AuraValidationError):
        ConnectionProfile.from_env(
            environ={"AURA_ADDR": "auras://h:7000/app", "AURA_TLS_CA": "/no/such/ca.pem"}
        )


def test_connection_profile_missing_addr_raises() -> None:
    with pytest.raises(AuraValidationError):
        ConnectionProfile.from_env(environ={})


def test_connection_profile_invalid_timeout() -> None:
    with pytest.raises(AuraValidationError):
        ConnectionProfile.from_env(
            environ={"AURA_ADDR": "aura://h:7000/app", "AURA_CONNECT_TIMEOUT_MS": "not-an-int"}
        )
    with pytest.raises(AuraValidationError):
        ConnectionProfile.from_env(
            environ={"AURA_ADDR": "aura://h:7000/app", "AURA_REQUEST_TIMEOUT_MS": "-5"}
        )


def test_connection_profile_serializable_alias() -> None:
    # The deprecated ``serializable`` token normalizes to snapshot, unchanged.
    profile = ConnectionProfile(addr="aura://h:7000/app", isolation="serializable")
    assert profile.isolation == "snapshot"
    from_env = ConnectionProfile.from_env(
        environ={"AURA_ADDR": "aura://h:7000/app", "AURA_ISOLATION": "serializable"}
    )
    assert from_env.isolation == "snapshot"


def test_connection_profile_timeouts_map_to_seconds() -> None:
    profile = ConnectionProfile.from_env(
        environ={
            "AURA_ADDR": "aura://h:7000/app",
            "AURA_CONNECT_TIMEOUT_MS": "2500",
            "AURA_REQUEST_TIMEOUT_MS": "9000",
        }
    )
    config = profile.to_client_config()
    assert config.connect_timeout_s == pytest.approx(2.5)
    assert config.request_timeout_s == pytest.approx(9.0)


def test_profile_repr_redacts_secret() -> None:
    profile = ConnectionProfile(addr="aura://h:7000/app", auth_token="leak-me")
    assert "leak-me" not in repr(profile)
    assert "***" in repr(profile)
    # No token -> repr shows None, not a secret.
    bare = ConnectionProfile(addr="aura://h:7000/app")
    assert "auth_token=None" in repr(bare)


async def test_client_from_profile() -> None:
    # A memory-scheme profile builds a working client with no server process.
    profile = ConnectionProfile.from_env(environ={"AURA_ADDR": "aura+memory://localhost/test"})
    async with Aura.from_profile(profile) as client:
        assert not client.is_closed
        # The resolved config reflects the profile's DSN.
        assert client.config.is_memory
