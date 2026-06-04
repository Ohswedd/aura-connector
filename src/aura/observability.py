"""Observability: in-process metrics, query fingerprinting, and OpenTelemetry hooks.

This module implements the library-level observability layer. It collects the full
metric set the connector exposes (query count by operation, query latency p50/p95/p99,
serialization/deserialization time, retry count, bytes sent/received, stream
cancellations) entirely in-process with no third-party dependency, computes a stable,
secret-free *query fingerprint* for a Query IR, and exposes an optional OpenTelemetry
span bridge.

The bridge is deliberately soft: if ``opentelemetry`` is not installed it becomes a
no-op, so importing :mod:`aura` never requires the package and metrics still work. No
exporter is bundled — wiring an exporter/collector is deployment configuration, not a
library responsibility. When telemetry is enabled and an OpenTelemetry tracer is
available, real spans are emitted around requests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "LatencyHistogram",
    "Metrics",
    "TelemetryConfig",
    "query_fingerprint",
]

# Keys whose *values* must never appear in a fingerprint or span attribute, because a
# Query IR can carry bound literals/parameters that may be sensitive.
_REDACT_KEYS = frozenset({"value", "values", "query", "params", "rows", "set", "key"})
_REDACTED = "?"


def _percentile(ordered: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


@dataclass
class LatencyHistogram:
    """Records durations (seconds) and reports count, total, and percentiles.

    Samples are retained so percentiles are exact; a real deployment would cap or use a
    streaming sketch, but bounded determinism matters more for a client library's own
    metrics than memory at planet scale, and the cap is documented below.
    """

    name: str
    max_samples: int = 8192
    _samples: list[float] = field(default_factory=list, repr=False)
    _count: int = 0
    _total: float = 0.0

    def record(self, seconds: float) -> None:
        self._count += 1
        self._total += seconds
        if len(self._samples) < self.max_samples:
            self._samples.append(seconds)

    @property
    def count(self) -> int:
        return self._count

    @property
    def total_seconds(self) -> float:
        return self._total

    def snapshot(self) -> dict[str, float | int]:
        ordered = sorted(self._samples)
        return {
            "count": self._count,
            "total_ms": round(self._total * 1000.0, 4),
            "p50_ms": round(_percentile(ordered, 50) * 1000.0, 4),
            "p95_ms": round(_percentile(ordered, 95) * 1000.0, 4),
            "p99_ms": round(_percentile(ordered, 99) * 1000.0, 4),
        }


class Metrics:
    """In-process collector for the connector's observability metric set.

    A single instance lives on each :class:`~aura.client.Client`. It is updated on the
    request hot path with cheap counter increments and histogram appends; ``snapshot``
    produces a JSON-ready dict for health endpoints, logs, or tests.
    """

    def __init__(self) -> None:
        self.query_count: dict[str, int] = {}
        self.error_count: dict[str, int] = {}
        self.retry_count = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.frames_sent = 0
        self.frames_received = 0
        self.stream_cancellations = 0
        self.request_latency = LatencyHistogram("request_latency")
        self.serialize_latency = LatencyHistogram("serialize")
        self.deserialize_latency = LatencyHistogram("deserialize")

    def record_query(self, operation: str) -> None:
        self.query_count[operation] = self.query_count.get(operation, 0) + 1

    def record_error(self, code: str) -> None:
        self.error_count[code] = self.error_count.get(code, 0) + 1

    @property
    def errors_total(self) -> int:
        return sum(self.error_count.values())

    def record_retry(self, attempts_beyond_first: int = 1) -> None:
        if attempts_beyond_first > 0:
            self.retry_count += attempts_beyond_first

    def record_bytes(self, sent: int, received: int) -> None:
        self.bytes_sent += sent
        self.bytes_received += received

    def record_frames(self, sent: int, received: int) -> None:
        self.frames_sent += sent
        self.frames_received += received

    def record_stream_cancellation(self) -> None:
        self.stream_cancellations += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "query_count": dict(sorted(self.query_count.items())),
            "error_count": dict(sorted(self.error_count.items())),
            "errors_total": self.errors_total,
            "retry_count": self.retry_count,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "stream_cancellations": self.stream_cancellations,
            "request_latency": self.request_latency.snapshot(),
            "serialize": self.serialize_latency.snapshot(),
            "deserialize": self.deserialize_latency.snapshot(),
        }

    def reset(self) -> None:
        self.query_count = {}
        self.error_count = {}
        self.retry_count = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.frames_sent = 0
        self.frames_received = 0
        self.stream_cancellations = 0
        self.request_latency = LatencyHistogram("request_latency")
        self.serialize_latency = LatencyHistogram("serialize")
        self.deserialize_latency = LatencyHistogram("deserialize")


def _redact(value: Any, *, redact: bool) -> Any:
    """Recursively replace literal values with a redaction marker, keeping structure."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in sorted(value.items()):
            child_redact = redact or key in _REDACT_KEYS
            out[key] = _redact(item, redact=child_redact)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(item, redact=redact) for item in value]
    if redact:
        return _REDACTED
    return value


def query_fingerprint(ir: dict[str, Any]) -> str:
    """Return a stable, secret-free fingerprint for a Query IR.

    The fingerprint captures the *shape* of a query — operation, model, the structure of
    its filters/sort/projection — with every literal and bind parameter redacted, so two
    queries that differ only in their values share one fingerprint and no value ever
    leaks. Format: ``"<operation>:<model>:<8-hex>"``.
    """
    operation = str(ir.get("operation", "query"))
    model = str(ir.get("model", ir.get("statement", "")) or "-")
    skeleton = _redact(ir, redact=False)
    canonical = json.dumps(skeleton, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    short_model = model.split()[0] if model else "-"
    return f"{operation}:{short_model}:{digest}"


@dataclass(frozen=True)
class TelemetryConfig:
    """Telemetry options matching the ``telemetry={...}`` connect argument."""

    opentelemetry: bool = False
    capture_query_ir: str = "off"  # "off" | "redacted"
    capture_bind_params: bool = False  # never enabled by default; bind params are secret

    @classmethod
    def from_value(cls, value: Any) -> TelemetryConfig:
        if value is None or value is False:
            return cls()
        if value is True:
            return cls(opentelemetry=True)
        if isinstance(value, TelemetryConfig):
            return value
        if isinstance(value, dict):
            return cls(
                opentelemetry=bool(value.get("opentelemetry", False)),
                capture_query_ir=str(value.get("capture_query_ir", "off")),
                capture_bind_params=bool(value.get("capture_bind_params", False)),
            )
        raise TypeError(f"Unsupported telemetry value: {type(value).__name__}")


def _load_tracer() -> Any | None:
    """Return an OpenTelemetry tracer if the package is importable, else ``None``."""
    try:  # pragma: no cover - exercised only when opentelemetry is installed
        from opentelemetry import trace  # type: ignore[import-not-found]
    except Exception:
        return None
    return trace.get_tracer("aura")  # pragma: no cover


class _TelemetryBridge:
    """Soft OpenTelemetry span bridge.

    When telemetry is enabled and a tracer is available, :meth:`span` opens a real span
    with non-sensitive attributes (operation, fingerprint, and — only if explicitly
    enabled — the redacted IR skeleton). Otherwise it is a zero-overhead no-op.
    """

    __slots__ = ("_config", "_tracer")

    def __init__(self, config: TelemetryConfig) -> None:
        self._config = config
        self._tracer = _load_tracer() if config.opentelemetry else None

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    @contextmanager
    def span(self, name: str, ir: dict[str, Any] | None = None) -> Iterator[None]:
        if self._tracer is None:
            yield
            return
        with self._tracer.start_as_current_span(name) as otel_span:  # pragma: no cover
            if ir is not None:
                otel_span.set_attribute("aura.operation", str(ir.get("operation", "")))
                otel_span.set_attribute("aura.query.fingerprint", query_fingerprint(ir))
                if self._config.capture_query_ir == "redacted":
                    otel_span.set_attribute(
                        "aura.query.ir",
                        json.dumps(_redact(ir, redact=False), sort_keys=True),
                    )
            try:
                yield
            except BaseException as exc:
                # Record the exception type/code on the span without leaking values.
                record = getattr(otel_span, "record_exception", None)
                if record is not None:
                    record(exc)
                code = getattr(exc, "code", type(exc).__name__)
                otel_span.set_attribute("aura.error.code", str(code))
                raise
