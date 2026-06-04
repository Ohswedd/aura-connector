"""Tiny benchmark helper. Produces real measured timings, never fabricated numbers."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable


def measure(name: str, fn: Callable[[], object], *, iterations: int = 10000) -> None:
    """Time ``fn`` over ``iterations`` runs and print real percentile latencies."""
    # Warm up.
    for _ in range(min(1000, iterations)):
        fn()
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1_000_000)  # microseconds
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95) - 1]
    p99 = samples[int(len(samples) * 0.99) - 1]
    total_ms = sum(samples) / 1000
    print(
        f"{name:<32} iters={iterations:>7}  "
        f"p50={p50:8.3f}us  p95={p95:8.3f}us  p99={p99:8.3f}us  total={total_ms:8.2f}ms"
    )
