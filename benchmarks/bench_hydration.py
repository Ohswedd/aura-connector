"""Benchmark: model hydration vs a naive baseline.

Compares Aura hydration against a naive ``dict`` round-trip baseline on the same
payload, reporting the real measured ratio. No fabricated numbers.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _bench import measure
from aura import AuraModel, Field
from aura.hydration import Hydrator


class Order(AuraModel):
    id: int = Field(primary_key=True)
    amount_cents: int
    currency: str
    note: str | None = Field(default=None)


ROWS = [
    {"id": i, "amount_cents": i * 100, "currency": "USD", "note": f"order {i}"} for i in range(1000)
]
_HYDRATOR = Hydrator()


def hydrate_batch() -> None:
    _HYDRATOR.hydrate_rows(Order, ROWS)


def baseline_batch() -> None:
    # Naive baseline: construct plain dicts (no validation, no typing).
    [dict(r) for r in ROWS]


if __name__ == "__main__":
    print("Hydration benchmark (real measured timings, 1000-row batches):")
    measure("hydrate_1000_orders", hydrate_batch, iterations=200)

    # Report the measured ratio against the naive baseline.
    def _time(fn, n=200):
        for _ in range(20):
            fn()
        start = time.perf_counter()
        for _ in range(n):
            fn()
        return time.perf_counter() - start

    h = _time(hydrate_batch)
    b = _time(baseline_batch)
    print(
        f"hydration/baseline ratio: {h / b:.2f}x "
        f"(hydration adds typing, validation, and post-load hooks)"
    )
