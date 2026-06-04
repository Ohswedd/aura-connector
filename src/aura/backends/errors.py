"""Backend error types and the driver-import helper.

The public backend error classes live in :mod:`aura.errors` (the single source of truth
for the Aura exception taxonomy) and are re-exported here for convenient access from
backend modules. :func:`import_driver` centralizes the "optional driver is not installed"
path so every backend raises an identical, actionable :class:`AuraDriverNotInstalledError`
with the exact ``pip install`` command for its extra.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from ..errors import (
    AuraBackendCapabilityError,
    AuraBackendError,
    AuraDialectError,
    AuraDriverNotInstalledError,
)

__all__ = [
    "AuraBackendCapabilityError",
    "AuraBackendError",
    "AuraDialectError",
    "AuraDriverNotInstalledError",
    "DRIVER_EXTRAS",
    "import_driver",
]

#: Maps a driver module name to the Aura extra that installs it. Used to build the precise
#: install command in :func:`import_driver`.
DRIVER_EXTRAS: dict[str, str] = {
    "aiosqlite": "sqlite",
    "asyncpg": "postgres",
    "aiomysql": "mysql",
    "motor": "mongodb",
    "redis": "redis",
}


def import_driver(
    module: str, *, extra: str | None = None, backend: str | None = None
) -> ModuleType:
    """Import an optional database driver, or raise an actionable install error.

    Parameters
    ----------
    module:
        The driver's importable module name (e.g. ``"asyncpg"``).
    extra:
        The Aura extra that provides it; defaults to the mapping in :data:`DRIVER_EXTRAS`.
    backend:
        The backend family name, included in the error context for diagnostics.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        resolved_extra = extra or DRIVER_EXTRAS.get(module, module)
        raise AuraDriverNotInstalledError(
            f"The {module!r} driver is required for the "
            f"{backend or resolved_extra} backend but is not installed. "
            f"Install with: pip install aura-connector[{resolved_extra}]",
            context={"driver": module, "extra": resolved_extra, "backend": backend or ""},
        ) from exc
