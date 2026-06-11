"""Typed access to search snippets/highlights on hydrated model instances.

When a query requests snippets via
:meth:`~aura.query.builder.QueryBuilder.snippets`, an AuraDB v1.5.0+ server
attaches, per result row, a plain-text snippet per snippet-eligible field with the
matched byte ranges highlighted. The hydrator stores the raw payload on the
instance; this module exposes it as typed value objects instead of dict lookups.

Snippet text is **plain text** and the connector makes no HTML/markup claims —
render and escape as appropriate for your output target.

The server emits highlight ranges as **byte** offsets into the fragment's UTF-8
text (its native representation). For Python ergonomics the connector converts them
to **character** offsets into :attr:`SearchSnippetFragment.text`, so
``frag.text[r.start : r.end]`` slices the matched span directly — including for
multibyte/accented text — with no manual byte handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "HighlightRange",
    "SearchSnippet",
    "SearchSnippetFragment",
    "search_snippets",
]


@dataclass(frozen=True)
class HighlightRange:
    """A highlighted range within a fragment's text (``start..end``).

    The offsets are **character** offsets into :attr:`SearchSnippetFragment.text`
    (the fragment), not the original source document, so ``frag.text[start:end]``
    slices the matched span. (The connector converts the server's native byte
    offsets to character offsets for Python ergonomics.)
    """

    start: int
    end: int


@dataclass(frozen=True)
class SearchSnippetFragment:
    """One plain-text fragment of a field with its highlighted ranges."""

    text: str
    ranges: tuple[HighlightRange, ...] = ()


@dataclass(frozen=True)
class SearchSnippet:
    """The snippet for one field of one result row: its fragments, in order."""

    field: str
    fragments: tuple[SearchSnippetFragment, ...] = ()


def _byte_to_char(encoded: bytes, byte_offset: int) -> int:
    """Convert a byte offset into UTF-8 ``encoded`` to a character offset, clamped
    to valid bounds and snapped down to a character boundary if it lands mid-codepoint.
    """
    off = max(0, min(byte_offset, len(encoded)))
    # `errors="ignore"` drops a partial trailing codepoint, snapping the offset down
    # to the nearest character boundary; the prefix length is the character offset.
    return len(encoded[:off].decode("utf-8", "ignore"))


def _fragment(raw: Any) -> SearchSnippetFragment:
    text = str(raw.get("text", "")) if isinstance(raw, dict) else ""
    ranges_raw = raw.get("ranges", []) if isinstance(raw, dict) else []
    encoded = text.encode("utf-8")
    ranges = tuple(
        HighlightRange(
            start=_byte_to_char(encoded, int(r["start"])),
            end=_byte_to_char(encoded, int(r["end"])),
        )
        for r in ranges_raw
        if isinstance(r, dict) and "start" in r and "end" in r
    )
    return SearchSnippetFragment(text=text, ranges=ranges)


def search_snippets(instance: Any) -> tuple[SearchSnippet, ...]:
    """Return the typed snippets attached to a hydrated ``instance``.

    Empty when the query did not request snippets or the server returned none, so
    callers can iterate unconditionally without a key check or a crash.
    """
    data = getattr(instance, "__dict__", {})
    raw = data.get("__snippets__")
    if not raw:
        return ()
    out: list[SearchSnippet] = []
    for snip in raw:
        if not isinstance(snip, dict):
            continue
        fragments = tuple(_fragment(f) for f in snip.get("fragments", []))
        out.append(SearchSnippet(field=str(snip.get("field", "")), fragments=fragments))
    return tuple(out)
