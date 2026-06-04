"""Unit tests for backend construction and URL parsing in the registry."""

from __future__ import annotations

from aura.backends.registry import parse_backend_url, resolve_backend
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge
from aura.transport.memory import MemoryTransport, ReferenceServer


def _telemetry() -> _TelemetryBridge:
    return _TelemetryBridge(TelemetryConfig())


def test_parse_postgres_url_components() -> None:
    url = parse_backend_url("postgresql://alice:secret@db.example.com:6000/shop?sslmode=require")
    assert url.family == "postgres"
    assert url.host == "db.example.com"
    assert url.port == 6000
    assert url.username == "alice"
    assert url.password == "secret"
    assert url.database == "shop"
    assert url.options["sslmode"] == "require"


def test_parse_default_ports() -> None:
    assert parse_backend_url("postgres://localhost/app").port == 5432
    assert parse_backend_url("mysql://localhost/app").port == 3306
    assert parse_backend_url("mongodb://localhost/app").port == 27017
    assert parse_backend_url("redis://localhost/0").port == 6379


def test_parse_sqlite_file_path() -> None:
    url = parse_backend_url("sqlite:///var/data/app.db")
    assert url.family == "sqlite"
    assert url.database == "/var/data/app.db"


def test_resolve_uses_supplied_transport_for_protocol_backends() -> None:
    transport = MemoryTransport(ReferenceServer())
    _config, backend = resolve_backend(
        "aura+memory://localhost/app",
        metrics=Metrics(),
        telemetry=_telemetry(),
        transport=transport,
    )
    assert backend.name == "memory"
    assert backend.transport is transport


def test_resolve_sqlite_builds_config_with_database() -> None:
    config, backend = resolve_backend(
        "sqlite:///tmp/app.db", metrics=Metrics(), telemetry=_telemetry()
    )
    assert backend.name == "sqlite"
    assert config.database == "/tmp/app.db"


def test_credentials_are_not_in_config_repr() -> None:
    config, _backend = resolve_backend(
        "postgresql://alice:secret@localhost/app", metrics=Metrics(), telemetry=_telemetry()
    )
    assert "secret" not in repr(config)
