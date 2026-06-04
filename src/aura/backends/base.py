"""The backend adapter interface.

A *backend* executes queries; a *transport* moves bytes. The AuraDB backend speaks the
Aura Wire Protocol over a transport; SQL backends speak to a database driver; the MongoDB
backend speaks to the document driver; the memory backend uses the in-process reference
engine. They all satisfy the single :class:`Backend` contract below, so the
:class:`~aura.client.Client` drives every store through one uniform interface and never
branches on backend type.

The backend layer is value-in / value-out: it receives canonical Query IR dicts (the same
IR the query builder emits) and returns :class:`BackendResult` rows as plain dicts. Model
hydration, the model registry, metrics aggregation, and the public query API all live above
this layer in the client, keeping each backend small and focused on its store.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .capabilities import BackendCapabilities

if TYPE_CHECKING:
    from ..models import AuraModel
    from ..schema.migrations import MigrationPlan

__all__ = ["Backend", "BackendResult"]


@dataclass
class BackendResult:
    """The normalized result of executing one Query IR against a backend.

    ``rows`` are raw row dicts keyed by storage name (the client hydrates them into model
    instances). ``affected`` is set for mutations, ``count`` for ``count``/``exists`` and
    as a row count for reads, and ``metadata`` carries backend-specific extras such as a
    cursor token (``cursor``/``has_more``) or a resolved traversal target (``model``).
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    affected: int | None = None
    count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: int | None = None


class Backend(abc.ABC):
    """Abstract storage backend.

    Concrete backends are constructed by the registry from a DSN and a resolved
    configuration. They perform no IO at construction time — only :meth:`connect` and the
    request methods touch the network or filesystem.
    """

    #: Stable backend family name (e.g. ``"sqlite"``, ``"postgres"``, ``"auradb"``).
    name: str = "backend"

    # -- lifecycle ---------------------------------------------------------------
    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish the backend connection. Called once before any request."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release all resources. Safe to call repeatedly."""

    @abc.abstractmethod
    async def ping(self) -> bool:
        """Round-trip a liveness check; return ``True`` when the backend is reachable."""

    # -- execution ---------------------------------------------------------------
    @abc.abstractmethod
    async def execute_query(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        """Execute a read Query IR (``select``/``count``/``exists``/``raw``/``traverse``)."""

    @abc.abstractmethod
    async def execute_mutation(self, ir: dict[str, Any], *, txid: int = 0) -> BackendResult:
        """Execute a mutation Query IR (``insert``/``update``/``delete``/``upsert``)."""

    @abc.abstractmethod
    def stream_query(
        self, ir: dict[str, Any], batch_size: int, *, txid: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a read query's rows page-by-page, holding at most one page in memory."""

    # -- schema ------------------------------------------------------------------
    @abc.abstractmethod
    async def create_schema(self, models: Iterable[type[AuraModel]]) -> None:
        """Create the storage objects (tables/collections/indexes) for ``models``."""

    @abc.abstractmethod
    async def drop_schema(self, models: Iterable[type[AuraModel]]) -> None:
        """Drop the storage objects for ``models``."""

    @abc.abstractmethod
    async def diff_schema(self, models: Iterable[type[AuraModel]]) -> MigrationPlan:
        """Return a local :class:`~aura.schema.migrations.MigrationPlan` for ``models``.

        The diff is model-based and computed locally; *applying* it live is only available
        on backends whose capabilities declare ``schema_migrations``.
        """

    # -- transactions ------------------------------------------------------------
    async def begin(self, txid: int, isolation: str) -> None:
        """Begin a transaction. Backends without transactions raise a capability error."""
        self._require("transactions")

    async def commit(self, txid: int, isolation: str) -> None:
        """Commit a transaction."""
        self._require("transactions")

    async def rollback(self, txid: int, isolation: str) -> None:
        """Roll back a transaction."""
        self._require("transactions")

    # -- introspection -----------------------------------------------------------
    @abc.abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Return this backend's immutable capability declaration."""

    def describe(self) -> dict[str, Any]:
        """Return a non-sensitive diagnostic description (no credentials)."""
        return {"backend": self.name, "capabilities": self.capabilities().to_dict()}

    def stats(self) -> dict[str, int]:
        """Return backend IO counters for health/observability. Default: empty."""
        return {}

    # -- helpers -----------------------------------------------------------------
    def _require(self, capability: str) -> None:
        """Raise :class:`AuraBackendCapabilityError` if ``capability`` is unsupported."""
        if not self.capabilities().supports(capability):
            from ..errors import AuraBackendCapabilityError

            raise AuraBackendCapabilityError(
                f"Backend {self.name!r} does not support {capability!r}",
                context={"backend": self.name, "capability": capability},
            )

    async def __aenter__(self) -> Backend:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
