"""Unit tests for DSN-based backend routing.

Routing must never import a database driver, so these run with no drivers installed and
assert only on the backend family each scheme selects.
"""

from __future__ import annotations

import pytest

from aura.backends.registry import SCHEME_FAMILY, backend_family, resolve_backend
from aura.errors import AuraError
from aura.observability import Metrics, TelemetryConfig, _TelemetryBridge


def _resolve(dsn: str):
    metrics = Metrics()
    telemetry = _TelemetryBridge(TelemetryConfig())
    return resolve_backend(dsn, metrics=metrics, telemetry=telemetry)


SCHEME_EXPECTATIONS = [
    ("aura://localhost:7171/app", "auradb"),
    ("auras://localhost:7171/app", "auradb"),
    ("aura+tcp://localhost/app", "auradb"),
    ("aura+memory://localhost/app", "memory"),
    ("memory://localhost/app", "memory"),
    ("sqlite:///tmp/app.db", "sqlite"),
    ("sqlite+aiosqlite:///tmp/app.db", "sqlite"),
    ("postgres://u:p@localhost:5432/app", "postgres"),
    ("postgresql://u:p@localhost:5432/app", "postgres"),
    ("postgresql+asyncpg://u:p@localhost/app", "postgres"),
    ("mysql://root:pw@localhost:3306/app", "mysql"),
    ("mysql+aiomysql://root:pw@localhost/app", "mysql"),
    ("mariadb://root:pw@localhost/app", "mysql"),
    ("mariadb+aiomysql://root:pw@localhost/app", "mysql"),
    ("mongodb://localhost:27017/app", "mongodb"),
    ("mongodb+motor://localhost/app", "mongodb"),
    ("redis://localhost:6379/0", "redis"),
    ("redis+asyncio://localhost:6379/0", "redis"),
]


@pytest.mark.parametrize(("dsn", "family"), SCHEME_EXPECTATIONS)
def test_backend_family_for_each_scheme(dsn: str, family: str) -> None:
    assert backend_family(dsn) == family


@pytest.mark.parametrize(("dsn", "family"), SCHEME_EXPECTATIONS)
def test_resolve_backend_selects_the_right_backend(dsn: str, family: str) -> None:
    _config, backend = _resolve(dsn)
    # The backend's family name is derived from the scheme; routing imports no driver.
    assert backend.name == family
    assert backend.capabilities().name == family


_FAMILIES = {"auradb", "memory", "sqlite", "postgres", "mysql", "mongodb", "redis"}


def test_every_scheme_in_the_map_resolves() -> None:
    # Guard against a scheme being added to the map without a resolver.
    for scheme in SCHEME_FAMILY:
        dsn = f"{scheme}://localhost/app" if "sqlite" not in scheme else f"{scheme}:///tmp/a.db"
        _config, backend = _resolve(dsn)
        assert backend.name in _FAMILIES


def test_unknown_scheme_raises_config_error() -> None:
    with pytest.raises(AuraError) as info:
        backend_family("cassandra://localhost/app")
    assert info.value.code == "config_error"
