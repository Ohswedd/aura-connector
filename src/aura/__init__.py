"""Aura — async-native, type-safe Python client and connector for AuraDB.

Public API surface. Internal modules (protocol, transport internals, hydration) are
available but the names exported here are the stable, user-facing entry points.
"""

from __future__ import annotations

from ._native import native_status
from .backends import (
    Backend,
    BackendCapabilities,
    BackendResult,
    resolve_backend,
)
from .client import Aura, Client, LeaderRedirect, Transaction, connect
from .config import (
    ClientConfig,
    PasswordAuth,
    PoolConfig,
    RetryPolicy,
    TLSConfig,
    TokenAuth,
    parse_dsn,
)
from .errors import (
    AuraAuthenticationError,
    AuraAuthorizationError,
    AuraBackendCapabilityError,
    AuraBackendError,
    AuraClientClosedError,
    AuraConnectionError,
    AuraConstraintError,
    AuraConstraintViolation,
    AuraDialectError,
    AuraDriverNotInstalledError,
    AuraError,
    AuraMigrationError,
    AuraNonRetryableTransactionError,
    AuraNotFoundError,
    AuraNotLeaderError,
    AuraProtocolError,
    AuraProtocolVersionError,
    AuraQueryError,
    AuraRetryableTransactionError,
    AuraSchemaError,
    AuraSerializationError,
    AuraServerError,
    AuraTimeoutError,
    AuraTransactionError,
    AuraValidationError,
    RelationshipNotLoadedError,
)
from .fields import Field, FieldInfo
from .models import AuraModel, get_model, registered_models
from .observability import LatencyHistogram, Metrics, TelemetryConfig, query_fingerprint
from .query import (
    FieldReference,
    QueryBuilder,
    and_,
    not_,
    or_,
)
from .schema import (
    Change,
    LockImpactEstimate,
    MigrationPlan,
    ModelSchema,
    diff_schemas,
    generate_migration,
    schema_document,
    schema_json,
)
from .vectors import Vector

#: ``Model`` is a friendly alias for :class:`AuraModel`.
Model = AuraModel

__all__ = [
    # client
    "Aura",
    "Client",
    "LeaderRedirect",
    "Transaction",
    "connect",
    # models / fields / vectors
    "AuraModel",
    "Model",
    "Field",
    "FieldInfo",
    "Vector",
    "get_model",
    "registered_models",
    # query
    "QueryBuilder",
    "FieldReference",
    "and_",
    "or_",
    "not_",
    # schema
    "ModelSchema",
    "schema_document",
    "schema_json",
    # migrations
    "Change",
    "LockImpactEstimate",
    "MigrationPlan",
    "diff_schemas",
    "generate_migration",
    # observability
    "Metrics",
    "LatencyHistogram",
    "TelemetryConfig",
    "query_fingerprint",
    # native acceleration diagnostics (optional in-repo extra)
    "native_status",
    # backends
    "Backend",
    "BackendCapabilities",
    "BackendResult",
    "resolve_backend",
    # config
    "ClientConfig",
    "parse_dsn",
    "TokenAuth",
    "PasswordAuth",
    "TLSConfig",
    "RetryPolicy",
    "PoolConfig",
    # errors
    "AuraError",
    "AuraBackendError",
    "AuraBackendCapabilityError",
    "AuraDialectError",
    "AuraDriverNotInstalledError",
    "AuraConnectionError",
    "AuraClientClosedError",
    "AuraTimeoutError",
    "AuraProtocolError",
    "AuraProtocolVersionError",
    "AuraQueryError",
    "AuraValidationError",
    "AuraSchemaError",
    "AuraMigrationError",
    "AuraAuthenticationError",
    "AuraAuthorizationError",
    "AuraServerError",
    "AuraNotFoundError",
    "AuraNotLeaderError",
    "AuraConstraintError",
    "AuraConstraintViolation",
    "AuraSerializationError",
    "AuraTransactionError",
    "AuraRetryableTransactionError",
    "AuraNonRetryableTransactionError",
    "RelationshipNotLoadedError",
]

__version__ = "0.4.1"
