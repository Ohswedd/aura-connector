"""Opt-in search snippets/highlights and honest capability gating.

AuraDB v1.5.0 produces **live, opt-in** plain-text snippets over the wire. Request
them on a ranked search with ``.snippets(fields=[...])`` and read the typed models
back with ``aura.search_snippets(row)``:

    SearchSnippet(field, fragments=(
        SearchSnippetFragment(text, ranges=(HighlightRange(start, end), ...)),
    ))

Snippet text is **plain text** — the ranges are byte offsets into the fragment text,
and the connector makes no HTML/markup claim (escape it yourself when rendering).

A snippet request is **capability-gated**: against a backend that does not advertise
``search_snippets`` it raises ``AuraCapabilityError`` rather than being silently
dropped. This example runs against the in-process reference engine, which does not
produce snippets, so it demonstrates that honest gating; point it at a real
``auradb://`` v1.5 server and the snippets come back populated.

Run against the in-process reference engine:
    python examples/auradb_snippet_search.py
"""

from __future__ import annotations

import asyncio

from aura import Aura, Field, Model, Vector, search_snippets
from aura.errors import AuraCapabilityError, AuraQueryError


class Doc(Model):
    id: int = Field(primary_key=True)
    body: str
    embedding: Vector[3]


async def main() -> None:
    async with Aura.connect("aura+memory://localhost/example", models=[Doc]) as client:
        await client.insert(
            Doc(
                id=1, body="create, verify, and restore an AuraDB backup", embedding=[1.0, 0.0, 0.0]
            )
        )

        # A search WITHOUT a snippet request never returns snippets.
        rows = await client.search(Doc).search_text("body", "restore").all()
        for row in rows:
            assert search_snippets(row) == ()  # empty tuple, never an error
        print("opt-in only: no .snippets() -> no snippets")

        # .snippets() requires a prior ranked-text clause and a non-empty field list.
        try:
            client.search(Doc).snippets(fields=["body"])
        except AuraQueryError as exc:
            print(f"snippets() without search_text is rejected: {exc}")

        # Requesting snippets is capability-gated. Against this reference engine
        # (no search_snippets capability) it raises; against a real auradb:// v1.5
        # server the rows come back with populated, field-allowlisted snippets:
        #
        #     for row in rows:
        #         for snip in search_snippets(row):
        #             for frag in snip.fragments:
        #                 for r in frag.ranges:
        #                     print(snip.field, frag.text[r.start : r.end])
        try:
            await (
                client.search(Doc)
                .search_text("body", "restore backup")
                .snippets(fields=["body"], max_fragments=2)
                .all()
            )
        except AuraCapabilityError as exc:
            print(f"snippets gated on this backend: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
