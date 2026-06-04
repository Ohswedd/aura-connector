"""Tests for DSN parsing and configuration."""

from __future__ import annotations

import pytest

from aura.config import (
    PasswordAuth,
    PoolConfig,
    RetryPolicy,
    TokenAuth,
    parse_dsn,
)
from aura.errors import AuraError


def test_parse_basic_dsn() -> None:
    cfg = parse_dsn("aura://localhost:7171/app")
    assert cfg.scheme == "aura"
    assert cfg.host == "localhost"
    assert cfg.port == 7171
    assert cfg.database == "app"
    assert cfg.address == "localhost:7171"


def test_default_port() -> None:
    assert parse_dsn("aura://localhost").port == 7171


def test_tls_scheme_enables_tls() -> None:
    assert parse_dsn("auras://host/db").tls.enabled is True
    assert parse_dsn("aura://host/db").tls.enabled is False


def test_memory_scheme() -> None:
    assert parse_dsn("aura+memory://localhost/x").is_memory is True


def test_query_parameters() -> None:
    cfg = parse_dsn(
        "aura://h:1/db?connect_timeout=2.5&request_timeout=9&compression=true"
        "&payload_checksum=false&max_payload_bytes=1024&tenant=acme"
    )
    assert cfg.connect_timeout_s == 2.5
    assert cfg.request_timeout_s == 9
    assert cfg.compression is True
    assert cfg.payload_checksum is False
    assert cfg.max_payload_bytes == 1024
    assert cfg.options == {"tenant": "acme"}


def test_userinfo_becomes_password_auth() -> None:
    cfg = parse_dsn("aura://alice:secret@host/db")
    assert isinstance(cfg.auth, PasswordAuth)
    assert cfg.auth.username == "alice"
    assert "***" in repr(cfg.auth)
    assert "secret" not in repr(cfg.auth)


def test_token_auth_redacts_secret() -> None:
    auth = TokenAuth(token="supersecret")
    assert "supersecret" not in repr(auth)
    assert auth.headers()["authorization"] == "Bearer supersecret"


def test_config_repr_redacts_auth() -> None:
    cfg = parse_dsn("aura://host/db", auth=TokenAuth("zzz"))
    assert "zzz" not in repr(cfg)


def test_invalid_scheme() -> None:
    with pytest.raises(AuraError):
        parse_dsn("postgres://host/db")


def test_empty_dsn() -> None:
    with pytest.raises(AuraError):
        parse_dsn("")


def test_retry_policy_backoff_is_bounded() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_s=0.1, max_delay_s=0.5, multiplier=2.0)
    assert policy.delay_for_attempt(1) == 0.0
    assert policy.delay_for_attempt(2) == 0.1
    assert policy.delay_for_attempt(3) == 0.2
    assert policy.delay_for_attempt(10) == 0.5  # capped


def test_invalid_retry_policy() -> None:
    with pytest.raises(AuraError):
        RetryPolicy(max_attempts=0)


def test_invalid_pool_config() -> None:
    with pytest.raises(AuraError):
        PoolConfig(min_size=10, max_size=1)


def test_percent_encoded_credentials_are_decoded() -> None:
    # Password "p@ss:word/!" percent-encoded in the userinfo section.
    cfg = parse_dsn("aura://al%2Fice:p%40ss%3Aword%2F%21@host/db")
    assert isinstance(cfg.auth, PasswordAuth)
    assert cfg.auth.username == "al/ice"
    assert cfg.auth.password == "p@ss:word/!"
    assert "p@ss" not in repr(cfg.auth)


def test_scheme_is_case_insensitive() -> None:
    cfg = parse_dsn("AURA+MEMORY://Host/Db")
    assert cfg.scheme == "aura+memory"
    assert cfg.is_memory is True


def test_max_payload_floor_enforced() -> None:
    with pytest.raises(AuraError):
        parse_dsn("aura://host/db?max_payload_bytes=8")


def test_explicit_auth_overrides_userinfo() -> None:
    cfg = parse_dsn("aura://alice:secret@host/db", auth=TokenAuth("tok"))
    assert isinstance(cfg.auth, TokenAuth)
