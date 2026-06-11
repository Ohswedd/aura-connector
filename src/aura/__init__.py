"""Aura — async-native, type-safe Python client and connector for AuraDB.

Public API surface. Internal modules (protocol, transport internals, hydration) are
available but the names exported here are the stable, user-facing entry points.
"""

from __future__ import annotations

from ._native import native_status
from .analyzers import ANALYZER_PRESETS, AnalyzerOptions
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
    AuraCapabilityError,
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
from .profiles import ConnectionProfile
from .query import (
    AggregateGroup,
    AggregateResult,
    FieldReference,
    GroupByResult,
    HnswOptions,
    Page,
    QueryBuilder,
    QueryProfile,
    SearchPage,
    SearchResultPage,
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
from .scores import SearchScores, search_scores
from .search_quality import (
    AnalyzerComparisonReport,
    AnalyzerLeg,
    ExactAnnComparisonReport,
    SearchEvalMetrics,
    SearchEvalQueryResult,
    SearchEvalReport,
)
from .snippets import (
    HighlightRange,
    SearchSnippet,
    SearchSnippetFragment,
    search_snippets,
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
    "SearchResultPage",
    "Page",
    "SearchPage",
    "AggregateResult",
    "AggregateGroup",
    "GroupByResult",
    "QueryProfile",
    "HnswOptions",
    "FieldReference",
    "and_",
    "or_",
    "not_",
    # search scores
    "SearchScores",
    "search_scores",
    # search snippets / highlights
    "SearchSnippet",
    "SearchSnippetFragment",
    "HighlightRange",
    "search_snippets",
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
    "ConnectionProfile",
    "parse_dsn",
    "TokenAuth",
    "PasswordAuth",
    "TLSConfig",
    "RetryPolicy",
    "PoolConfig",
    # search-quality report parsing
    "SearchEvalReport",
    "SearchEvalMetrics",
    "SearchEvalQueryResult",
    "ExactAnnComparisonReport",
    "AnalyzerComparisonReport",
    "AnalyzerLeg",
    # analyzers (AuraDB v1.5.0)
    "AnalyzerOptions",
    "ANALYZER_PRESETS",
    # errors
    "AuraError",
    "AuraBackendError",
    "AuraBackendCapabilityError",
    "AuraCapabilityError",
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

__version__ = "0.9.0"
