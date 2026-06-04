"""Tests for the observability layer: metrics, percentiles, fingerprinting."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from aura import LatencyHistogram, Metrics, TelemetryConfig, query_fingerprint
from aura.observability import _TelemetryBridge


def test_latency_histogram_percentiles() -> None:
    hist = LatencyHistogram("t")
    for i in range(1, 101):  # 1ms .. 100ms
        hist.record(i / 1000.0)
    snap = hist.snapshot()
    assert snap["count"] == 100
    # p50 ~ 50ms, p99 ~ 99ms (nearest-rank interpolation tolerance).
    assert 49.0 <= snap["p50_ms"] <= 51.0
    assert 98.0 <= snap["p99_ms"] <= 100.0
    assert snap["p95_ms"] <= snap["p99_ms"]
    assert abs(snap["total_ms"] - sum(range(1, 101))) < 1.0


def test_empty_histogram_is_zero() -> None:
    snap = LatencyHistogram("t").snapshot()
    assert snap == {"count": 0, "total_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0}


def test_metrics_counters() -> None:
    m = Metrics()
    m.record_query("select")
    m.record_query("select")
    m.record_query("insert")
    m.record_retry(2)
    m.record_bytes(100, 250)
    m.record_frames(1, 1)
    m.record_stream_cancellation()
    snap = m.snapshot()
    assert snap["query_count"] == {"insert": 1, "select": 2}
    assert snap["retry_count"] == 2
    assert snap["bytes_sent"] == 100
    assert snap["bytes_received"] == 250
    assert snap["stream_cancellations"] == 1


def test_metrics_error_recording() -> None:
    m = Metrics()
    m.record_error("constraint_violation")
    m.record_error("constraint_violation")
    m.record_error("timeout")
    assert m.errors_total == 3
    snap = m.snapshot()
    assert snap["error_count"] == {"constraint_violation": 2, "timeout": 1}
    assert snap["errors_total"] == 3


def test_metrics_reset() -> None:
    m = Metrics()
    m.record_query("select")
    m.record_bytes(10, 10)
    m.record_error("timeout")
    m.reset()
    snap = m.snapshot()
    assert snap["query_count"] == {}
    assert snap["error_count"] == {}
    assert snap["errors_total"] == 0
    assert snap["bytes_sent"] == 0
    assert snap["request_latency"]["count"] == 0


def test_fingerprint_is_stable_across_literal_values() -> None:
    """Two queries differing only in literal values share one fingerprint."""
    ir_a = {
        "operation": "select",
        "model": "User",
        "filters": [
            {
                "kind": "compare",
                "op": "eq",
                "left": {"field": "email"},
                "right": {"kind": "literal", "value": "alice@example.com"},
            }
        ],
    }
    ir_b = {
        "operation": "select",
        "model": "User",
        "filters": [
            {
                "kind": "compare",
                "op": "eq",
                "left": {"field": "email"},
                "right": {"kind": "literal", "value": "bob@secret.example"},
            }
        ],
    }
    fp_a = query_fingerprint(ir_a)
    fp_b = query_fingerprint(ir_b)
    assert fp_a == fp_b
    assert fp_a.startswith("select:User:")


def test_fingerprint_does_not_leak_values() -> None:
    ir = {
        "operation": "raw",
        "statement": "SELECT * FROM User",
        "params": {"token": "super-secret-value"},
    }
    fp = query_fingerprint(ir)
    assert "super-secret-value" not in fp


def test_fingerprint_distinguishes_structure() -> None:
    fp_eq = query_fingerprint(
        {
            "operation": "select",
            "model": "User",
            "filters": [{"kind": "compare", "op": "eq", "left": {"field": "id"}}],
        }
    )
    fp_gt = query_fingerprint(
        {
            "operation": "select",
            "model": "User",
            "filters": [{"kind": "compare", "op": "gt", "left": {"field": "id"}}],
        }
    )
    assert fp_eq != fp_gt


def test_telemetry_config_from_value() -> None:
    assert TelemetryConfig.from_value(None) == TelemetryConfig()
    assert TelemetryConfig.from_value(True).opentelemetry is True
    cfg = TelemetryConfig.from_value({"opentelemetry": True, "capture_query_ir": "redacted"})
    assert cfg.opentelemetry is True
    assert cfg.capture_query_ir == "redacted"
    assert cfg.capture_bind_params is False  # never defaults on


def test_bridge_is_noop_without_otel() -> None:
    """With telemetry requested but no opentelemetry installed, span() is a no-op."""
    bridge = _TelemetryBridge(TelemetryConfig(opentelemetry=True))
    # Either OTel is genuinely absent (enabled False) — exercised in CI — or present.
    with bridge.span("aura.select", {"operation": "select", "model": "User"}):
        pass  # must not raise regardless


def test_bridge_is_disabled_when_telemetry_off() -> None:
    bridge = _TelemetryBridge(TelemetryConfig(opentelemetry=False))
    assert bridge.enabled is False
    with bridge.span("aura.select", {"operation": "select", "model": "User"}):
        pass


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    @contextmanager
    def start_as_current_span(self, name: str):
        span = _FakeSpan()
        self.spans.append(span)
        yield span


def _bridge_with_tracer(config: TelemetryConfig, tracer: _FakeTracer) -> _TelemetryBridge:
    bridge = _TelemetryBridge(TelemetryConfig(opentelemetry=False))
    object.__setattr__(bridge, "_config", config)
    object.__setattr__(bridge, "_tracer", tracer)
    return bridge


def test_bridge_emits_span_with_redacted_attributes() -> None:
    tracer = _FakeTracer()
    bridge = _bridge_with_tracer(
        TelemetryConfig(opentelemetry=True, capture_query_ir="redacted"), tracer
    )
    ir = {
        "operation": "select",
        "model": "User",
        "filters": [
            {
                "kind": "compare",
                "op": "eq",
                "left": {"field": "email"},
                "right": {"kind": "literal", "value": "alice@secret.example"},
            }
        ],
    }
    with bridge.span("aura.select", ir):
        pass
    assert len(tracer.spans) == 1
    attrs = tracer.spans[0].attributes
    assert attrs["aura.operation"] == "select"
    assert attrs["aura.query.fingerprint"].startswith("select:User:")
    # The captured IR must not leak the literal value.
    assert "alice@secret.example" not in attrs["aura.query.ir"]


def test_bridge_records_exception_on_span() -> None:
    tracer = _FakeTracer()
    bridge = _bridge_with_tracer(TelemetryConfig(opentelemetry=True), tracer)

    class _Boom(Exception):
        code = "boom_code"

    with pytest.raises(_Boom), bridge.span("aura.select", {"operation": "select", "model": "User"}):
        raise _Boom("kaboom")
    span = tracer.spans[0]
    assert len(span.exceptions) == 1
    assert span.attributes["aura.error.code"] == "boom_code"
