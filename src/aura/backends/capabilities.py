"""Backend capability declarations.

A :class:`BackendCapabilities` value is the honest, machine-readable statement of what a
backend can and cannot do. Feature parity across backends is *represented*, never faked:
the client reads capabilities to decide whether an operation is supported and raises
:class:`~aura.errors.AuraBackendCapabilityError` when it is not, instead of silently
emulating a missing feature.

Every backend returns a frozen instance from :meth:`~aura.backends.base.Backend.capabilities`.
The set of flags is deliberately small and concrete; each maps to an observable behaviour.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["BackendCapabilities", "CAPABILITY_FLAGS"]

#: The capability flags, in declaration order. Used to render the capability matrix and
#: to validate capability names passed to :meth:`BackendCapabilities.require`.
CAPABILITY_FLAGS: tuple[str, ...] = (
    "transactions",
    "schema_create",
    "schema_migrations",
    "json_fields",
    "relationships",
    "graph_traversal",
    "vector_search",
    "hybrid_search",
    "full_text_search",
    "raw_queries",
    "explain",
    "server_side_cursors",
    "native_protocol",
    "document_queries",
    "key_value",
)


@dataclass(frozen=True)
class BackendCapabilities:
    """An immutable description of one backend's supported features.

    ``name`` identifies the backend family (for example ``"sqlite"`` or ``"auradb"``).
    Every other attribute is a boolean answering "can this backend do X?". Defaults are
    conservative (``False``) so a new backend must opt in to each capability explicitly
    rather than accidentally over-claim.
    """

    name: str
    transactions: bool = False
    schema_create: bool = False
    schema_migrations: bool = False
    json_fields: bool = False
    relationships: bool = False
    graph_traversal: bool = False
    vector_search: bool = False
    hybrid_search: bool = False
    full_text_search: bool = False
    raw_queries: bool = False
    explain: bool = False
    server_side_cursors: bool = False
    native_protocol: bool = False
    document_queries: bool = False
    key_value: bool = False

    def supports(self, capability: str) -> bool:
        """Return whether ``capability`` (one of :data:`CAPABILITY_FLAGS`) is supported."""
        if capability not in CAPABILITY_FLAGS:
            raise ValueError(f"Unknown capability {capability!r}")
        return bool(getattr(self, capability))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict of the backend name plus every capability flag."""
        return asdict(self)
