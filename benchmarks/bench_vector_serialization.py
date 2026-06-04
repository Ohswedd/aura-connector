"""Benchmark: vector construction and serialization."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _bench import measure
from aura import Vector

_RAW_1536 = [float(i) / 1536 for i in range(1536)]
_VEC = Vector[1536](_RAW_1536)


def construct_1536() -> None:
    Vector[1536](_RAW_1536)


def serialize_1536() -> None:
    _VEC.to_list()


if __name__ == "__main__":
    print("Vector serialization benchmark (real measured timings, 1536-dim):")
    measure("construct_validate_1536", construct_1536, iterations=2000)
    measure("serialize_1536", serialize_1536, iterations=5000)
