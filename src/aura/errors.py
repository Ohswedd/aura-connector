"""Structured exception taxonomy for the Aura client.

Every Aura error derives from :class:`AuraError` and carries a stable, machine
readable ``code``, a human message, a retry classification, and optional context
such as a server ``request_id``. Errors never embed secrets: configuration objects
redact credentials in their ``repr`` and error messages only reference field names,
never values.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AuraAuthenticationError",
    "AuraAuthorizationError",
    "AuraClientClosedError",
    "AuraConnectionError",
    "AuraConstraintError",
    "AuraConstraintViolation",
    "AuraError",
    "AuraMigrationError",
    "AuraNonRetryableTransactionError",
    "AuraNotFoundError",
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


class RelationshipNotLoadedError(AuraError):
    """Raised when an un-included relationship is accessed.

    Aura never performs hidden lazy network IO; accessing a relationship
    that was not explicitly ``include``-d raises this error instead.
    """

    default_code = "relationship_not_loaded"
