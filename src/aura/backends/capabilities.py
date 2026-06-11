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

from ..errors import AuraBackendCapabilityError

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
    "group_by",
    "query_profile",
    "hnsw_preview",
    "cursor_resume",
    "query_analyzers",
    "search_snippets",
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
    group_by: bool = False
    query_profile: bool = False
    hnsw_preview: bool = False
    cursor_resume: bool = False
    #: Live over-the-wire query-time analyzer selection on ranked text search
    #: (AuraDB v1.5.0+). Negotiated from the server's advertised ``query_analyzers``
    #: capability; a non-default analyzer against a server without it raises a
    #: capability error rather than being silently dropped.
    query_analyzers: bool = False
    #: Live, opt-in server-produced search snippets/highlights on ranked text search
    #: (AuraDB v1.5.0+). Negotiated from the server's advertised ``search_snippets``
    #: capability.
    search_snippets: bool = False

    def supports(self, capability: str) -> bool:
        """Return whether ``capability`` (one of :data:`CAPABILITY_FLAGS`) is supported."""
        if capability not in CAPABILITY_FLAGS:
            raise ValueError(f"Unknown capability {capability!r}")
        return bool(getattr(self, capability))

    def require(self, capability: str) -> None:
        """Assert that ``capability`` is supported, raising otherwise.

        Raises :class:`ValueError` for an unknown capability name and
        :class:`~aura.errors.AuraBackendCapabilityError` when the capability is
        known but this backend does not provide it. The error names the backend and
        the missing capability so callers can branch on capabilities instead of
        catching a generic failure — Aura never silently emulates a missing feature.
        """
        if not self.supports(capability):
            raise AuraBackendCapabilityError(
                f"backend {self.name!r} does not support capability {capability!r}",
                context={"backend": self.name, "capability": capability},
            )

    def describe(self) -> dict[str, Any]:
        """Return a human/JSON-friendly summary of this backend's capabilities.

        The result has the backend ``name`` plus ``supported`` and ``unsupported``
        lists (each a subset of :data:`CAPABILITY_FLAGS`, in declaration order).
        """
        supported = [flag for flag in CAPABILITY_FLAGS if bool(getattr(self, flag))]
        unsupported = [flag for flag in CAPABILITY_FLAGS if not bool(getattr(self, flag))]
        return {"name": self.name, "supported": supported, "unsupported": unsupported}

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict of the backend name plus every capability flag."""
        return asdict(self)
