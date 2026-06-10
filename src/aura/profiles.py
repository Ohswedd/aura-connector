"""Connection profiles: environment-driven convenience configuration.

A :class:`ConnectionProfile` is a small, immutable convenience layer over a DSN
plus the handful of operational knobs deployments commonly read from the
environment (auth token, TLS CA, SNI server name, timeouts, default isolation).

It is **not** a secret manager. The auth token it carries is whatever your
deployment's secret management already resolved into the environment; the profile
redacts it from ``repr`` so it does not leak into logs, but storing, rotating, and
protecting that secret is the deployment's responsibility, not this object's.

A profile resolves to a :class:`~aura.config.ClientConfig` via the same
:func:`~aura.config.parse_dsn` path as every other entry point, so it adds no new
connection behaviour — only a typed, redacted, env-friendly way to assemble the
inputs. ``DEFAULT_ISOLATION`` stays ``snapshot`` and the deprecated
``serializable`` token still normalizes to ``snapshot``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import DEFAULT_ISOLATION, _normalize_isolation
from .config import ClientConfig, TLSConfig, TokenAuth, parse_dsn
from .errors import AuraValidationError

__all__ = ["ConnectionProfile"]

#: Default connect/request timeouts in milliseconds. These mirror the
#: :class:`~aura.config.ClientConfig` defaults (10s / 30s) expressed in ms, which
#: is the unit operators set in the environment.
_DEFAULT_CONNECT_TIMEOUT_MS = 10_000
_DEFAULT_REQUEST_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class ConnectionProfile:
    """An immutable, environment-friendly description of how to connect.

    ``addr`` is an ``aura://`` DSN (the same form :func:`~aura.config.parse_dsn`
    accepts). The remaining fields are optional overrides layered on top of it.
    The auth token is redacted from ``repr``.
    """

    addr: str
    auth_token: str | None = None
    tls_ca: str | None = None
    server_name: str | None = None
    connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS
    request_timeout_ms: int = _DEFAULT_REQUEST_TIMEOUT_MS
    isolation: str = DEFAULT_ISOLATION

    def __post_init__(self) -> None:
        if not isinstance(self.addr, str) or not self.addr.strip():
            raise AuraValidationError(
                "ConnectionProfile.addr must be a non-empty DSN string", code="config_error"
            )
        for name, value in (
            ("connect_timeout_ms", self.connect_timeout_ms),
            ("request_timeout_ms", self.request_timeout_ms),
        ):
            # ``bool`` is an ``int`` subclass; reject it explicitly so a stray
            # ``True`` does not masquerade as a 1ms timeout.
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise AuraValidationError(
                    f"ConnectionProfile.{name} must be a positive integer (milliseconds)",
                    code="config_error",
                )
        # Normalize isolation so a profile never carries a non-canonical token;
        # the deprecated ``serializable`` alias maps to ``snapshot`` unchanged.
        object.__setattr__(self, "isolation", _normalize_isolation(self.isolation))

    def __repr__(self) -> str:
        token = "***" if self.auth_token else None
        return (
            "ConnectionProfile("
            f"addr={self.addr!r}, auth_token={token!r}, tls_ca={self.tls_ca!r}, "
            f"server_name={self.server_name!r}, connect_timeout_ms={self.connect_timeout_ms}, "
            f"request_timeout_ms={self.request_timeout_ms}, isolation={self.isolation!r})"
        )

    @classmethod
    def from_env(
        cls, prefix: str = "AURA", *, environ: Mapping[str, str] | None = None
    ) -> ConnectionProfile:
        """Build a profile from environment variables.

        Reads ``<PREFIX>_ADDR`` (required) plus optional ``<PREFIX>_AUTH_TOKEN``,
        ``<PREFIX>_TLS_CA``, ``<PREFIX>_SERVER_NAME``,
        ``<PREFIX>_CONNECT_TIMEOUT_MS``, ``<PREFIX>_REQUEST_TIMEOUT_MS``, and
        ``<PREFIX>_ISOLATION``. ``environ`` overrides the source mapping (defaults
        to :data:`os.environ`), which makes the loader trivially testable.

        Raises :class:`~aura.errors.AuraValidationError` when the address is
        missing, a timeout is not a positive integer, or a provided TLS CA path
        does not exist.
        """
        env: Mapping[str, str] = os.environ if environ is None else environ

        def key(name: str) -> str:
            return f"{prefix}_{name}" if prefix else name

        addr = env.get(key("ADDR"))
        if not addr:
            raise AuraValidationError(
                f"{key('ADDR')} is required to build a ConnectionProfile", code="config_error"
            )

        def timeout(name: str, default: int) -> int:
            raw = env.get(key(name))
            if raw is None or raw.strip() == "":
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise AuraValidationError(
                    f"{key(name)} must be an integer number of milliseconds, got {raw!r}",
                    code="config_error",
                ) from exc
            if value <= 0:
                raise AuraValidationError(
                    f"{key(name)} must be a positive number of milliseconds, got {value}",
                    code="config_error",
                )
            return value

        profile = cls(
            addr=addr,
            auth_token=env.get(key("AUTH_TOKEN")) or None,
            tls_ca=env.get(key("TLS_CA")) or None,
            server_name=env.get(key("SERVER_NAME")) or None,
            connect_timeout_ms=timeout("CONNECT_TIMEOUT_MS", _DEFAULT_CONNECT_TIMEOUT_MS),
            request_timeout_ms=timeout("REQUEST_TIMEOUT_MS", _DEFAULT_REQUEST_TIMEOUT_MS),
            isolation=env.get(key("ISOLATION")) or DEFAULT_ISOLATION,
        )
        profile.validate_tls()
        return profile

    def validate_tls(self) -> None:
        """Validate that the TLS CA path, when set, points at an existing file.

        Raises :class:`~aura.errors.AuraValidationError` otherwise. This is the one
        piece of filesystem IO a profile performs, and only when a CA path is set.
        """
        if self.tls_ca is not None and not os.path.isfile(self.tls_ca):
            raise AuraValidationError(
                f"TLS CA path does not exist or is not a file: {self.tls_ca!r}",
                code="config_error",
            )

    def _overrides(self) -> dict[str, Any]:
        """Build the keyword overrides threaded through :func:`parse_dsn`."""
        self.validate_tls()
        overrides: dict[str, Any] = {
            "connect_timeout_s": self.connect_timeout_ms / 1000.0,
            "request_timeout_s": self.request_timeout_ms / 1000.0,
        }
        if self.auth_token is not None:
            overrides["auth"] = TokenAuth(self.auth_token)
        if self.tls_ca is not None:
            overrides["tls"] = TLSConfig(enabled=True, ca_cert_path=self.tls_ca)
        if self.server_name is not None:
            # Carried as a transport option; the bundled TCP transport consumes it
            # as the TLS SNI server name (see aura.transport.tcp).
            overrides["options"] = {"tls_server_name": self.server_name}
        return overrides

    def to_client_config(self) -> ClientConfig:
        """Resolve this profile to a fully-built :class:`~aura.config.ClientConfig`.

        Uses the same :func:`~aura.config.parse_dsn` path as every other entry
        point, so the result is identical to connecting with the equivalent DSN and
        keyword arguments. Validates the TLS CA path if one is set.
        """
        return parse_dsn(self.addr, **self._overrides())
