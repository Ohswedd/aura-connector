"""Unit tests for the missing-driver error path.

When a backend's driver package is not installed, connecting must raise
:class:`AuraDriverNotInstalledError` with the exact ``pip install`` command for its extra.
These tests run only for drivers that are actually absent in the environment, so they pass
whether or not the optional extras are installed.
"""

from __future__ import annotations

import importlib.util

import pytest

from aura import Aura
from aura.backends.errors import import_driver
from aura.errors import AuraDriverNotInstalledError

DRIVERS = [
    ("postgresql://u:p@localhost/app", "asyncpg", "postgres"),
    ("mysql://root:pw@localhost/app", "aiomysql", "mysql"),
    ("mongodb://localhost/app", "motor", "mongodb"),
    ("redis://localhost:6379/0", "redis", "redis"),
]


def _absent(module: str) -> bool:
    return importlib.util.find_spec(module.split(".")[0]) is None


@pytest.mark.parametrize(("dsn", "driver", "extra"), DRIVERS)
async def test_connect_raises_when_driver_missing(dsn: str, driver: str, extra: str) -> None:
    if not _absent(driver):
        pytest.skip(f"{driver} is installed; missing-driver path not exercised")
    with pytest.raises(AuraDriverNotInstalledError) as info:
        await Aura.connect(dsn)
    message = str(info.value)
    assert f"pip install aura-connector[{extra}]" in message
    assert info.value.context["extra"] == extra


def test_import_driver_message_names_the_extra() -> None:
    with pytest.raises(AuraDriverNotInstalledError) as info:
        import_driver("a_driver_that_does_not_exist", extra="postgres", backend="postgres")
    assert "pip install aura-connector[postgres]" in str(info.value)


def test_import_driver_returns_installed_module() -> None:
    # aiosqlite is installed for the default test run via the sqlite extra.
    if _absent("aiosqlite"):
        pytest.skip("aiosqlite not installed")
    module = import_driver("aiosqlite", backend="sqlite")
    assert module.__name__ == "aiosqlite"
