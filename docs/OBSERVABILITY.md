# Observability

Aura collects the full metric set listed below entirely **in-process**, with no
third-party dependency, and exposes an **optional** OpenTelemetry span bridge. No
exporter or collector is bundled, wiring an exporter is deployment configuration, not a
library concern, but every metric is available programmatically and via `client.health()`.

## Metrics

Every `Client` owns a `Metrics` instance, reachable as `client.metrics`:

```python
async with Client.connect("aura+memory://localhost/app", models=[User]) as client:
    await client.query(User).all()
    snapshot = client.metrics.snapshot()
```

`snapshot()` returns a JSON-ready dict:

| Key | Meaning |
|---|---|
| `query_count` | count of requests by operation (`select`, `insert`, `update`, `cursor_fetch`, …) |
| `error_count` / `errors_total` | failures by stable error code, and their sum |
| `retry_count` | number of retried attempts beyond the first |
| `bytes_sent` / `bytes_received` | wire bytes across all requests |
| `frames_sent` / `frames_received` | protocol frame counts |
| `stream_cancellations` | streams closed early by the consumer |
| `request_latency` | `{count, total_ms, p50_ms, p95_ms, p99_ms}` for full round trips |
| `serialize` | latency of encoding the request body |
| `deserialize` | latency of hydrating the response |

Errors are recorded both when the server returns an error frame (mapped to a typed
exception in `client._raise_error`) and when a transport-level failure is raised, so
`error_count` reflects every failed request by its stable code.

Percentiles are computed from retained samples (capped at `max_samples`, default 8192)
using nearest-rank interpolation. Call `client.metrics.reset()` to clear counters.

## Query fingerprinting

`query_fingerprint(ir)` returns a stable, **secret-free** identifier for a Query IR:

```python
from aura import query_fingerprint

ir = client.query(User).filter(User.email == "a@example.com").explain()
print(query_fingerprint(ir))   # e.g. "select:User:1a2b3c4d"
```

Two queries that differ only in their literal/bound values produce the **same**
fingerprint, and no value ever appears in the output, bind parameters and literals are
redacted before hashing. Use it to group queries in logs and metrics without leaking
data.

## OpenTelemetry

Pass `telemetry=` to `connect`:

```python
client = await Client.connect(
    dsn,
    models=[User],
    telemetry={"opentelemetry": True, "capture_query_ir": "redacted"},
)
```

- If `opentelemetry` is installed, Aura opens a real span around each request with
  non-sensitive attributes (`aura.operation`, `aura.query.fingerprint`, and, only when
  `capture_query_ir="redacted"`, the value-redacted IR skeleton). When the request fails,
  the span records the exception and an `aura.error.code` attribute (the stable code, not
  any value).
- If `opentelemetry` is **not** installed, telemetry silently becomes a no-op; metrics
  collection is unaffected. Importing `aura` never requires the package.
- `capture_bind_params` defaults to `False` and is never enabled implicitly; bind
  parameters are treated as secret.

## Boundary statement

> Aura emits telemetry to an installed OpenTelemetry SDK when enabled. It does not bundle
> collectors, agents, or deployment exporters. In-process metrics are always available
> through the client metrics API.

## Honest limitations

- No exporter/collector is shipped. Configure an OpenTelemetry SDK + exporter in your
  application to ship spans; Aura emits them to the active tracer.
- Server-side planning time and other server metrics require a live AuraDB server; the
  in-process metrics above are entirely client-side.
