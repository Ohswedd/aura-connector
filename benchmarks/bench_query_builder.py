"""Benchmark: Query IR construction cost."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _bench import measure
from aura import AuraModel, Field
from aura.query.builder import QueryBuilder, QueryResult


class User(AuraModel):
    id: int = Field(primary_key=True)
    email: str
    name: str


class _NullExecutor:
    async def run(self, node, model):
        return QueryResult()

    async def stream(self, node, model, batch_size):
        return
        yield


def build_simple() -> None:
    QueryBuilder(User, _NullExecutor()).where(User.id == 1).explain()


def build_complex() -> None:
    (
        QueryBuilder(User, _NullExecutor())
        .where(User.email.contains("@example.com"))
        .where(User.id > 100)
        .order_by(User.id.desc())
        .select(User.id, User.email)
        .limit(25)
        .offset(50)
        .explain()
    )


if __name__ == "__main__":
    print("Query builder benchmark (real measured timings):")
    measure("ir_build_simple_point", build_simple)
    measure("ir_build_complex_query", build_complex)
