"""The in-memory backend: the reference engine over the in-process protocol path.

The ``aura+memory://`` and ``memory://`` schemes route here. This backend drives the
:class:`~aura.transport.memory.MemoryTransport`, which runs the deterministic
:class:`~aura.transport.memory.ReferenceServer` in-process through the real codec — so the
full Aura Wire Protocol path (framing, execution, hydration) is exercised with no network
and no external service. It is what makes the examples and the default test suite run
everywhere, and the reference against which the database adapters are validated.
"""

from __future__ import annotations

from .auradb import ProtocolBackend
from .capabilities import BackendCapabilities

__all__ = ["MemoryBackend"]

_MEMORY_CAPABILITIES = BackendCapabilities(
    name="memory",
    transactions=True,
    schema_create=True,
    schema_migrations=True,
    json_fields=True,
    relationships=True,
    graph_traversal=True,
    vector_search=True,
    hybrid_search=True,
    full_text_search=True,
    raw_queries=True,
    explain=True,
    server_side_cursors=True,
    native_protocol=True,
    document_queries=True,
    key_value=False,
)


class MemoryBackend(ProtocolBackend):
    """Reference backend driving the in-process :class:`ReferenceServer`.

    It shares the full protocol execution path with :class:`~aura.backends.auradb.AuraDBBackend`
    (it *is* a :class:`~aura.backends.auradb.ProtocolBackend`) and declares the same feature
    set the reference engine implements.
    """

    name = "memory"

    def capabilities(self) -> BackendCapabilities:
        return _MEMORY_CAPABILITIES
