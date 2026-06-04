# Errors

Every Aura error derives from `AuraError` and carries a stable, machine-readable
`code`, a human `message`, a `retryable` classification, an optional server
`request_id`, and non-sensitive structured `context`. Errors never embed secrets:
configuration objects redact credentials in their `repr`.

## Taxonomy

| Exception | Code | Retryable | Raised when |
|---|---|---|---|
| `AuraError` | `aura_error` | no | Base class. |
| `AuraConnectionError` | `connection_error` | yes | Connection cannot be established or is lost. |
| `AuraClientClosedError` | `client_closed` | no | Operation on a closed client. |
| `AuraTimeoutError` | `timeout` | yes | Operation exceeds its deadline. |
| `AuraProtocolError` | `protocol_error` | no | Malformed/invalid wire frame. |
| `AuraProtocolVersionError` | `protocol_version_error` | no | Incompatible server protocol version. |
| `AuraQueryError` | `query_error` | no | Invalid query or server rejection. |
| `AuraValidationError` | `validation_error` | no | Model/field validation failure. |
| `AuraSchemaError` | `schema_error` | no | Invalid model/schema definition. |
| `AuraAuthenticationError` | `authentication_error` | no | Authentication failed. |
| `AuraAuthorizationError` | `authorization_error` | no | Insufficient permission. |
| `AuraServerError` | `server_error` | yes | Server-side internal error. |
| `AuraNotFoundError` | `not_found` | no | Requested record does not exist. |
| `AuraConstraintError` | `constraint_violation` | no | Unique/primary-key constraint violated. |
| `AuraConstraintViolation` | `constraint_violation` | no | Alias of `AuraConstraintError` (same class). |
| `AuraMigrationError` | `migration_error` | no | A schema migration cannot be planned or applied. |
| `AuraSerializationError` | `serialization_error` | no | Value cannot be serialized. |
| `AuraTransactionError` | `transaction_error` | no | Transaction lifecycle error. |
| `AuraRetryableTransactionError` | `retryable_transaction_error` | yes | Transaction failed but may retry. |
| `AuraNonRetryableTransactionError` | `non_retryable_transaction_error` | no | Transaction failed, do not retry. |
| `RelationshipNotLoadedError` | `relationship_not_loaded` | no | Accessing an un-included relationship. |

## Handling

```python
from aura import AuraNotFoundError, AuraConstraintError, AuraError

try:
    user = await client.query(User).where(User.id == 1).one()
except AuraNotFoundError:
    ...
except AuraError as exc:
    log.warning("aura failure", code=exc.code, retryable=exc.retryable,
                request_id=exc.request_id)
    raise
```

## Retry classification

Only errors with `retryable=True` are retried by the client, bounded by
`RetryPolicy.max_attempts` with exponential backoff. Connection, timeout, server, and
retryable-transaction errors default to retryable; everything else does not.

## Observability

Every failed request is counted by its stable `code` in `client.metrics` (`error_count` /
`errors_total`), both server error frames and transport-level failures. When telemetry is
enabled with an OpenTelemetry tracer, the request span also records the exception and an
`aura.error.code` attribute (the code, never a value). See
[`OBSERVABILITY.md`](OBSERVABILITY.md).

## Redaction

`TokenAuth`, `PasswordAuth`, and `ClientConfig` redact secrets in `repr`, so credentials
never reach logs or tracebacks. Error messages reference field *names*, never
values.
