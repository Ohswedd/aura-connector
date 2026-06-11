"""Analyzer-aware search ergonomics and honest capability gating.

AuraDB v1.5.0 adds **live** query-time analyzer presets (``default`` / ``simple`` /
``ascii_fold`` / ``keyword`` / ``english_basic``). The connector lets you name an
analyzer on a ranked search — either ``search_text(..., analyzer="simple")`` or the
chained ``.analyzer("simple")``. Names are validated client-side, and a non-default
analyzer is sent over the wire to a v1.5 AuraDB server (``auradb://…``), which
advertises the ``query_analyzers`` capability.

A non-default analyzer is **capability-gated**: against a backend that does not
advertise ``query_analyzers`` it raises ``AuraCapabilityError`` rather than being
silently dropped. This example runs against the in-process reference engine, which
does not implement analyzers, so it demonstrates exactly that honest gating; the
``default`` analyzer is unaffected. Point it at a real ``auradb://`` v1.5 server and
the non-default analyzers succeed.

Run against the in-process reference engine:
    python examples/auradb_analyzer_search.py
"""

from __future__ import annotations

import asyncio

from aura import AnalyzerOptions, Aura, Field, Model, Vector, search_scores
from aura.errors import AuraCapabilityError


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


async def main() -> None:
    # Analyzer names are validated up front, with no silent fallback.
    assert AnalyzerOptions("ascii_fold").name == "ascii_fold"
    try:
        AnalyzerOptions("stemming")
    except Exception as exc:
        print(f"rejected unknown analyzer: {exc}")

    async with Aura.connect("aura+memory://localhost/example", models=[Doc]) as client:
        await client.insert(Doc(id=1, body="raft consensus raft", embedding=[1.0, 0.0, 0.0]))
        await client.insert(Doc(id=2, body="the raft module", embedding=[0.0, 1.0, 0.0]))

        # The default analyzer works exactly as before.
        rows = await client.search(Doc).search_text("body", "raft").all()
        print(f"default analyzer matched {len(rows)} docs:")
        for doc in rows:
            print(f"  #{search_scores(doc).rank} id={doc.id}")

        # Against a backend without the query_analyzers capability (this reference
        # engine), a non-default analyzer is gated — never silently ignored. Against
        # a real auradb:// v1.5 server these calls return analyzed results instead.
        try:
            await client.search(Doc).search_text("body", "raft", analyzer="simple").all()
        except AuraCapabilityError as exc:
            print(f"non-default analyzer gated on this backend: {exc}")

        # The chained form behaves the same way (here: english_basic).
        try:
            await client.search(Doc).search_text("body", "raft").analyzer("english_basic").all()
        except AuraCapabilityError as exc:
            print(f"chained analyzer also gated honestly: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
