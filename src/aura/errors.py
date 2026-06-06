"""Structured exception taxonomy for the Aura client.

Every Aura error derives from :class:`AuraError` and carries a stable, machine
readable ``code``, a human message, a retry classification, and optional context
such as a server ``request_id``. Errors never embed secrets: configuration objects
redact credentials in their ``repr`` and error messages only reference field names,
never values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "AuraAuthenticationError",
    "AuraAuthorizationError",
    "AuraBackendCapabilityError",
    "AuraBackendError",
    "AuraClientClosedError",
    "AuraConnectionError",
    "AuraConstraintError",
    "AuraConstraintViolation",
    "AuraDialectError",
    "AuraDriverNotInstalledError",
    "AuraError",
    "AuraMigrationError",
    "AuraNonRetryableTransactionError",
    "AuraNotFoundError",
    "AuraNotLeaderError",
    "AuraProtocolError",
    "AuraProtocolVersionError",
    "AuraQueryError",
    "AuraRetryableTransactionError",
    "AuraSchemaError",
    "AuraSerializationError",
    "AuraServerError",
    "AuraTimeoutError",
    "AuraTransactionError",
    "AuraValidationError",
    "RelationshipNotLoadedError",
]


class AuraError(Exception):
    """Base class for every error raised by the Aura client.

    Parameters
    ----------
    message:
        Human readable description. Must not contain secrets.
    code:
        Stable error code string (defaults to the class-level ``default_code``).
    retryable:
        Whether the operation may be safely retried.
    request_id:
        Optional server request identifier for correlation.
    context:
        Optional non-sensitive structured context (field names, counts, codes).
    """

    default_code = "aura_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        request_id: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.retryable = retryable
        self.request_id = request_id
        self.context: dict[str, Any] = dict(context or {})

    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.request_id is not None:
            parts.append(f"(request_id={self.request_id})")
        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"retryable={self.retryable}, request_id={self.request_id!r})"
        )


class AuraConnectionError(AuraError):
    """Raised when a connection cannot be established or is lost."""

    default_code = "connection_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class AuraClientClosedError(AuraError):
    """Raised when an operation is attempted on a closed client."""

    default_code = "client_closed"


class AuraTimeoutError(AuraError):
    """Raised when an operation exceeds its configured deadline."""

    default_code = "timeout"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class AuraProtocolError(AuraError):
    """Raised when a wire frame is malformed or violates protocol invariants."""

    default_code = "protocol_error"


class AuraProtocolVersionError(AuraProtocolError):
    """Raised when the server speaks an incompatible protocol version."""

    default_code = "protocol_version_error"


class AuraQueryError(AuraError):
    """Raised when a query is invalid or the server rejects it."""

    default_code = "query_error"


class AuraValidationError(AuraError):
    """Raised when model or field validation fails."""

    default_code = "validation_error"


class AuraSchemaError(AuraError):
    """Raised when a model or schema definition is invalid."""

    default_code = "schema_error"


class AuraAuthenticationError(AuraError):
    """Raised when authentication fails."""

    default_code = "authentication_error"


class AuraAuthorizationError(AuraError):
    """Raised when the authenticated identity lacks permission."""

    default_code = "authorization_error"


class AuraServerError(AuraError):
    """Raised when the server reports an internal error."""

    default_code = "server_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class AuraNotLeaderError(AuraError):
    """Raised when a write reaches an AuraDB node that is not the cluster leader.

    AuraDB's multi-node mode is an experimental, opt-in preview: only the Raft
    leader accepts writes, and a write sent to a follower is rejected with a
    ``not_leader`` response rather than being silently forwarded. The connector
    maps that response to this dedicated exception so applications can catch it
    specifically — distinct from a generic :class:`AuraServerError` — and decide
    how to react.

    The error surfaces every leader-routing hint the server provided so a caller
    can redirect without parsing the human message:

    * :attr:`leader_addr` — the best usable client-facing address of the current
      leader, when known. Pass this to
      :meth:`aura.Client.connect_to_leader` / :meth:`aura.Client.reconnect_to`.
    * :attr:`leader_client_addr` — the leader's declared client address as the
      server reported it (``leader_addr`` falls back to this).
    * :attr:`leader_hint` — a free-form leader hint string when one is provided.
    * :attr:`leader_node_id` — the recognized leader's node id, when known.
    * :attr:`current_node_id` — the id of the (non-leader) node that was reached.
    * :attr:`retryable` — whether the operation may succeed if retried against the
      leader. ``True`` when a leader is known. A ``True`` flag does **not** mean
      the connector retries writes automatically: redirecting a write is only safe
      when the caller knows the request was not already applied, so redirection is
      always explicit (see :meth:`aura.Client.connect_to_leader` and
      :meth:`aura.Client.with_leader_redirect`).
    * :attr:`raw_payload` — the full structured server payload, for diagnostics.

    Any field is ``None`` when the server did not provide it (for example a
    follower that does not yet know who the leader is). The class never raises on
    missing fields.
    """

    default_code = "not_leader"

    def __init__(
        self,
        message: str,
        *,
        leader_hint: str | None = None,
        leader_addr: str | None = None,
        leader_client_addr: str | None = None,
        leader_node_id: str | None = None,
        current_node_id: str | None = None,
        raw_payload: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)
        self.leader_client_addr = leader_client_addr
        # ``leader_addr`` is the canonical usable address for a redirect: prefer an
        # explicit ``leader_addr``, then the declared client address, then a hint.
        self.leader_addr = leader_addr or leader_client_addr or leader_hint
        self.leader_hint = leader_hint or self.leader_addr
        self.leader_node_id = leader_node_id
        self.current_node_id = current_node_id
        self.raw_payload: Mapping[str, Any] | None = raw_payload

    def __str__(self) -> str:
        # A single, readable line that names the node reached, where the leader is
        # (or that it is unknown), the retry classification, and how to redirect.
        # It only ever prints node ids and a host:port leader address — never auth
        # tokens, TLS material, or any other field that could carry a secret.
        parts = [f"[{self.code}] {self.message}"]
        if self.current_node_id:
            parts.append(f"(reached non-leader node {self.current_node_id})")
        if self.leader_addr:
            parts.append(f"(leader at {self.leader_addr})")
        elif self.leader_node_id:
            parts.append(f"(leader node {self.leader_node_id}, address unknown)")
        else:
            parts.append("(leader unknown)")
        if self.leader_addr:
            # A leader is reachable: redirecting is safe, but never automatic for
            # writes — say so explicitly so callers do not assume a silent retry.
            parts.append(
                "(retry on the leader with Client.connect_to_leader(error) or "
                f"reconnect_to({self.leader_addr!r}); writes are not retried automatically)"
            )
        else:
            # No usable address: do not imply a retry is safe; point at leader discovery.
            parts.append("(resolve the leader, e.g. `auradb cluster leader`, then reconnect)")
        if self.request_id is not None:
            parts.append(f"(request_id={self.request_id})")
        return " ".join(parts)

    @classmethod
    def from_server_payload(
        cls,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        request_id: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AuraNotLeaderError:
        """Build a :class:`AuraNotLeaderError` from a decoded server error payload.

        Extracts the leader hint from every field AuraDB may send, whether at the
        top level of the payload or nested under a ``not_leader`` object, and never
        crashes when a field is absent. ``payload`` is any mapping (the wire error
        payload, or a connector ``context`` dict).
        """
        data: dict[str, Any] = dict(payload or {})
        nested = data.get("not_leader")
        nested_map: dict[str, Any] = dict(nested) if isinstance(nested, Mapping) else {}

        def pick(*names: str) -> str | None:
            for source in (nested_map, data):
                for name in names:
                    value = source.get(name)
                    if value:
                        return str(value)
            return None

        if retryable is None:
            flag = data.get("retryable")
            retryable = bool(flag) if flag is not None else None
        return cls(
            message,
            code=code,
            retryable=retryable if retryable is not None else True,
            request_id=request_id,
            context=dict(data),
            leader_client_addr=pick("leader_client_addr"),
            leader_addr=pick("leader_addr", "leader_client_addr"),
            leader_hint=pick("leader_hint"),
            leader_node_id=pick("leader_node_id", "leader_id"),
            current_node_id=pick("current_node_id"),
            raw_payload=dict(data) or None,
        )


class AuraNotFoundError(AuraError):
    """Raised when a requested record does not exist."""

    default_code = "not_found"


class AuraConstraintError(AuraError):
    """Raised when a unique/primary-key or other constraint is violated."""

    default_code = "constraint_violation"


#: ``AuraConstraintViolation`` is an alias for the same exception class.
AuraConstraintViolation = AuraConstraintError


class AuraMigrationError(AuraError):
    """Raised when a schema migration cannot be planned or applied."""

    default_code = "migration_error"


class AuraSerializationError(AuraError):
    """Raised when a value cannot be serialized into a protocol payload."""

    default_code = "serialization_error"


class AuraTransactionError(AuraError):
    """Base class for transaction lifecycle errors."""

    default_code = "transaction_error"


class AuraRetryableTransactionError(AuraTransactionError):
    """Raised when a transaction fails but may be safely retried."""

    default_code = "retryable_transaction_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class AuraNonRetryableTransactionError(AuraTransactionError):
    """Raised when a transaction fails and must not be retried."""

    default_code = "non_retryable_transaction_error"


class AuraBackendError(AuraError):
    """Base class for errors raised by a storage backend adapter.

    Backends translate driver-specific failures into the Aura taxonomy. When no more
    specific Aura error applies, the adapter raises this with a stable ``code``.
    """

    default_code = "backend_error"


class AuraBackendCapabilityError(AuraBackendError):
    """Raised when an operation requires a capability the backend does not provide.

    The error carries the backend name and the missing capability in its ``context`` so
    callers can branch on capabilities rather than catch-and-guess. Aura never silently
    emulates a feature a backend lacks (for example vector search on a backend without a
    vector index); it raises this instead.
    """

    default_code = "backend_capability_error"


class AuraDialectError(AuraBackendError):
    """Raised when a query or type cannot be expressed in a backend's SQL dialect."""

    default_code = "dialect_error"


class AuraDriverNotInstalledError(AuraBackendError):
    """Raised when a backend's database driver package is not installed.

    The message includes the precise ``pip install`` command for the matching extra, for
    example ``pip install aura-connector[postgres]``.
    """

    default_code = "driver_not_installed"


class RelationshipNotLoadedError(AuraError):
    """Raised when an un-included relationship is accessed.

    Aura never performs hidden lazy network IO; accessing a relationship
    that was not explicitly ``include``-d raises this error instead.
    """

    default_code = "relationship_not_loaded"
