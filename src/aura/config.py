"""Connection configuration: DSN parsing and immutable config dataclasses.

This module performs no IO. It parses an ``aura://`` DSN and produces frozen
configuration objects for timeouts, retries, TLS, authentication, pooling, and
payload limits. Credentials are redacted in ``repr`` output so secrets never leak
into logs or tracebacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import AuraError

__all__ = [
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_PORT",
    "AuthConfig",
    "ClientConfig",
    "PasswordAuth",
    "PoolConfig",
    "RetryPolicy",
    "TLSConfig",
    "TokenAuth",
    "parse_dsn",
]

DEFAULT_PORT = 7171
DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB hard ceiling on a single frame
_VALID_SCHEMES = frozenset({"aura", "auras", "aura+tcp", "aura+memory"})


class _Redacted:
    """Sentinel rendered in place of secret values."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "***"


REDACTED = _Redacted()


@dataclass(frozen=True)
class AuthConfig:
    """Base authentication configuration. Subclasses carry the actual secret."""

    def headers(self) -> dict[str, str]:
        """Return auth metadata sent during the connection handshake."""
        return {}


@dataclass(frozen=True)
class TokenAuth(AuthConfig):
    """Bearer-token authentication.

    The token is stored but redacted from ``repr`` output.
    """

    token: str

    def __repr__(self) -> str:
        return "TokenAuth(token=***)"

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}


@dataclass(frozen=True)
class PasswordAuth(AuthConfig):
    """Username/password authentication. The password is redacted from ``repr``."""

    username: str
    password: str

    def __repr__(self) -> str:
        return f"PasswordAuth(username={self.username!r}, password=***)"

    def headers(self) -> dict[str, str]:
        return {"auth-username": self.username, "auth-password": self.password}


@dataclass(frozen=True)
class TLSConfig:
    """TLS configuration shape.

    Aura defaults to TLS for non-local connections. ``verify_hostname`` is on by
    default; certificate paths describe an mTLS setup. This object only captures the
    *shape* of TLS configuration — the transport applies it when establishing a
    real connection.
    """

    enabled: bool = False
    verify_hostname: bool = True
    ca_cert_path: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None
    pinned_sha256: str | None = None


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry policy with exponential backoff.

    Only operations classified as retryable are retried, and never more than
    ``max_attempts`` times.
    """

    max_attempts: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 2.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise AuraError("RetryPolicy.max_attempts must be >= 1", code="config_error")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise AuraError("RetryPolicy delays must be non-negative", code="config_error")
        if self.multiplier < 1:
            raise AuraError("RetryPolicy.multiplier must be >= 1", code="config_error")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the backoff delay (seconds) before the given 1-based attempt."""
        if attempt <= 1:
            return 0.0
        raw = self.base_delay_s * (self.multiplier ** (attempt - 2))
        return min(raw, self.max_delay_s)


@dataclass(frozen=True)
class PoolConfig:
    """Async connection-pool policy."""

    min_size: int = 1
    max_size: int = 16
    acquire_timeout_s: float = 5.0
    max_lifetime_s: float = 1800.0
    idle_timeout_s: float = 300.0

    def __post_init__(self) -> None:
        if self.min_size < 0:
            raise AuraError("PoolConfig.min_size must be >= 0", code="config_error")
        if self.max_size < 1:
            raise AuraError("PoolConfig.max_size must be >= 1", code="config_error")
        if self.min_size > self.max_size:
            raise AuraError("PoolConfig.min_size cannot exceed max_size", code="config_error")


@dataclass(frozen=True)
class ClientConfig:
    """Fully-resolved client configuration produced by :func:`parse_dsn`."""

    scheme: str
    host: str
    port: int
    database: str | None = None
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    compression: bool = False
    payload_checksum: bool = True
    auth: AuthConfig | None = None
    tls: TLSConfig = field(default_factory=TLSConfig)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    pool: PoolConfig = field(default_factory=PoolConfig)
    options: dict[str, str] = field(default_factory=dict)

    @property
    def is_memory(self) -> bool:
        """Whether this DSN selects the in-memory reference transport."""
        return self.scheme == "aura+memory"

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def with_overrides(self, **changes: Any) -> ClientConfig:
        """Return a copy with the given fields replaced."""
        return replace(self, **changes)

    def __repr__(self) -> str:
        auth_repr = repr(self.auth) if self.auth is not None else "None"
        return (
            f"ClientConfig(scheme={self.scheme!r}, host={self.host!r}, port={self.port}, "
            f"database={self.database!r}, tls={self.tls.enabled}, auth={auth_repr})"
        )


def _coerce_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_dsn(
    dsn: str,
    *,
    auth: AuthConfig | None = None,
    tls: TLSConfig | None = None,
    retry: RetryPolicy | None = None,
    pool: PoolConfig | None = None,
    **overrides: Any,
) -> ClientConfig:
    """Parse an ``aura://`` DSN into a :class:`ClientConfig`.

    Supported schemes: ``aura`` (plaintext TCP), ``auras`` (TLS TCP),
    ``aura+tcp`` (explicit TCP), and ``aura+memory`` (in-memory reference transport).

    Query parameters configure timeouts and behaviour, e.g.::

        aura://localhost:7171/app?connect_timeout=5&compression=true

    Raises :class:`AuraError` for malformed input.
    """
    if not isinstance(dsn, str) or not dsn:
        raise AuraError("DSN must be a non-empty string", code="config_error")

    parts = urlsplit(dsn)
    scheme = parts.scheme.lower()
    if scheme not in _VALID_SCHEMES:
        raise AuraError(
            f"Unsupported DSN scheme {scheme!r}; expected one of {sorted(_VALID_SCHEMES)}",
            code="config_error",
        )

    host = parts.hostname or "localhost"
    port = parts.port or DEFAULT_PORT
    database = parts.path.lstrip("/") or None

    tls_enabled = scheme == "auras"
    resolved_tls = tls or TLSConfig(enabled=tls_enabled)
    if tls is None and tls_enabled:
        resolved_tls = TLSConfig(enabled=True)

    if parts.username and auth is None:
        password = unquote(parts.password) if parts.password else ""
        auth = PasswordAuth(username=unquote(parts.username), password=password)

    query: dict[str, list[str]] = parse_qs(parts.query, keep_blank_values=True)
    flat: dict[str, str] = {key: values[-1] for key, values in query.items()}

    config_kwargs: dict[str, Any] = {
        "scheme": scheme,
        "host": host,
        "port": port,
        "database": database,
        "auth": auth,
        "tls": resolved_tls,
        "retry": retry or RetryPolicy(),
        "pool": pool or PoolConfig(),
    }

    known: dict[str, Any] = {}
    leftover: dict[str, str] = {}
    for key, value in flat.items():
        if key == "connect_timeout":
            known["connect_timeout_s"] = float(value)
        elif key == "request_timeout":
            known["request_timeout_s"] = float(value)
        elif key == "max_payload_bytes":
            known["max_payload_bytes"] = int(value)
        elif key == "compression":
            known["compression"] = _coerce_bool(value)
        elif key == "payload_checksum":
            known["payload_checksum"] = _coerce_bool(value)
        else:
            leftover[key] = value

    config_kwargs.update(known)
    config_kwargs["options"] = leftover
    config_kwargs.update(overrides)

    config = ClientConfig(**config_kwargs)
    if config.max_payload_bytes < 64:
        raise AuraError("max_payload_bytes must be at least 64", code="config_error")
    return config
