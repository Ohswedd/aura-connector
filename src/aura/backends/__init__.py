"""Backend adapter architecture.

A backend executes queries against a concrete store; the client drives every store through
the single :class:`Backend` interface. AuraDB remains the native, high-performance backend
over the Aura Wire Protocol; the SQL, document, and key-value adapters make Aura useful with
existing infrastructure. Feature differences are declared honestly through
:class:`BackendCapabilities` and enforced with :class:`~aura.errors.AuraBackendCapabilityError`.

See :doc:`/docs/BACKENDS` for the conceptual overview and
:doc:`/docs/BACKEND_CAPABILITY_MATRIX` for the per-backend feature matrix.
"""

from __future__ import annotations

from ..errors import (
    AuraBackendCapabilityError,
    AuraBackendError,
    AuraDialectError,
    AuraDriverNotInstalledError,
)
from .base import Backend, BackendResult
from .capabilities import CAPABILITY_FLAGS, BackendCapabilities
from .registry import SCHEME_FAMILY, BackendURL, backend_family, resolve_backend

__all__ = [
    "Backend",
    "BackendResult",
    "BackendCapabilities",
    "CAPABILITY_FLAGS",
    "BackendURL",
    "SCHEME_FAMILY",
    "backend_family",
    "resolve_backend",
    "AuraBackendError",
    "AuraBackendCapabilityError",
    "AuraDialectError",
    "AuraDriverNotInstalledError",
]
