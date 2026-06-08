"""Typed access to ranked-search scores on hydrated model instances.

When a query uses :meth:`~aura.query.builder.QueryBuilder.search_text`,
:meth:`~aura.query.builder.QueryBuilder.search_vector`, or
:meth:`~aura.query.builder.QueryBuilder.search_hybrid`, the server attaches a
relevance/similarity score (and, for hybrid, the component text and vector
scores plus a 1-based rank). The hydrator stores these on the instance; this
module exposes them as a typed value object instead of dunder attribute lookups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["SearchScores", "search_scores"]


@dataclass(frozen=True)
class SearchScores:
    """The ranked-search scores attached to one result row."""

    #: The primary (or fused, for hybrid) relevance/similarity score.
    score: float | None = None
    #: The BM25 text-relevance component (hybrid results).
    text_score: float | None = None
    #: The vector-similarity component (hybrid results).
    vector_score: float | None = None
    #: The 1-based rank within the ranked result set.
    rank: int | None = None


def search_scores(instance: Any) -> SearchScores:
    """Return the :class:`SearchScores` attached to a hydrated ``instance``.

    All fields are ``None`` when the query was not a ranked search.
    """
    data = getattr(instance, "__dict__", {})
    return SearchScores(
        score=data.get("__score__"),
        text_score=data.get("__text_score__"),
        vector_score=data.get("__vector_score__"),
        rank=data.get("__rank__"),
    )
