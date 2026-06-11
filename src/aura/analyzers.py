"""Query-time analyzer options for AuraDB search (matching AuraDB v1.5.0).

AuraDB v1.5.0 introduces a deterministic analyzer/tokenizer framework with the
built-in presets enumerated in :data:`ANALYZER_PRESETS`. This module gives the
connector a small, validated value object — :class:`AnalyzerOptions` — for naming
an analyzer, plus helpers used to gate analyzer-aware queries on a backend
capability.

**Honest scope.** AuraDB v1.5.0 makes analyzer selection a live, over-the-wire
parameter on ranked ``text_search`` **and** ``hybrid`` search, negotiated via the
server's additive ``query_analyzers`` capability. The connector validates analyzer
names client-side and gates a non-default analyzer on that capability, so a query
against a server that does not advertise it **degrades with a capability error** —
the connector never silently drops the request or pretends a backend applied an
analyzer it cannot. ``keyword`` is a first-class value here: it carries whole-field
exact-match semantics on both plain text search and the text side of a hybrid query.
The connector makes **no language claims** beyond the documented AuraDB preset
behaviors (``english_basic`` is a small deterministic helper, not a stemmer or full
NLP). See ``docs/SEARCH_AND_RANKING.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import AuraQueryError

__all__ = ["ANALYZER_PRESETS", "AnalyzerOptions", "requested_analyzer"]

#: The built-in AuraDB analyzer presets, matching AuraDB v1.5.0. ``default`` is the
#: v1.x tokenizer behavior under an explicit name (selecting it changes nothing).
#: ``english_basic`` is a small built-in helper (lowercase + a fixed stopword list +
#: a conservative plural fold), not a stemmer or full NLP.
ANALYZER_PRESETS: tuple[str, ...] = (
    "default",
    "simple",
    "ascii_fold",
    "keyword",
    "english_basic",
)


@dataclass(frozen=True)
class AnalyzerOptions:
    """A validated query-time analyzer selection.

    ``name`` must be one of :data:`ANALYZER_PRESETS`; an unknown name raises
    :class:`~aura.errors.AuraQueryError` at construction (client-side validation).
    The connector makes **no language claims** beyond the AuraDB preset behaviors.
    """

    name: str = "default"

    def __post_init__(self) -> None:
        if self.name not in ANALYZER_PRESETS:
            raise AuraQueryError(
                f"unknown analyzer {self.name!r}; expected one of {list(ANALYZER_PRESETS)}"
            )

    @property
    def is_default(self) -> bool:
        """Whether this is the ``default`` analyzer (a no-op preserving v1.x behavior)."""
        return self.name == "default"

    def to_ir(self) -> str:
        """The wire/IR value for this analyzer (its preset name)."""
        return self.name


def requested_analyzer(ir: Mapping[str, Any]) -> str | None:
    """Return the non-default analyzer a query IR asks for, or ``None``.

    Used by the backends to decide whether a query needs the ``query_analyzers``
    capability. A ``default`` (or absent) analyzer needs no capability and returns
    ``None`` so existing queries are completely unaffected.
    """
    for clause_key in ("text_search", "hybrid"):
        clause = ir.get(clause_key)
        if isinstance(clause, Mapping):
            analyzer = clause.get("analyzer")
            if isinstance(analyzer, str) and analyzer and analyzer != "default":
                return analyzer
    return None
