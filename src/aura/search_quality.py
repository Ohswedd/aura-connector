"""Typed parsers for AuraDB's search-quality report JSON.

AuraDB's ``auradb search eval`` command (server/CLI side, v1.4.0) measures
ranked-retrieval relevance — MRR@k, NDCG@k, Recall@k — on a relevance dataset and
emits a machine-readable JSON report. ``auradb vector eval`` similarly emits an
exact-vs-approximate recall/latency report.

These reports are produced by the AuraDB CLI, **not** by the connector: the
connector does not run server-side CLI commands and does not compute relevance
itself. This module is a convenience layer for reading those reports into typed,
immutable Python objects — useful when a deployment pipeline runs the CLI and
wants to assert on the numbers. The metrics are dataset-specific regression
signals, never universal benchmarks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from os import PathLike
from typing import Any

from .errors import AuraValidationError

__all__ = [
    "Bm25Params",
    "ExactAnnComparisonReport",
    "HybridWeights",
    "SearchEvalMetrics",
    "SearchEvalQueryResult",
    "SearchEvalReport",
]


def _require(data: Mapping[str, Any], key: str, what: str) -> Any:
    if key not in data:
        raise AuraValidationError(
            f"{what} is missing required field {key!r}", code="validation_error"
        )
    return data[key]


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuraValidationError(
            f"field {field!r} must be a number, got {type(value).__name__}",
            code="validation_error",
        )
    return float(value)


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuraValidationError(
            f"field {field!r} must be an integer, got {type(value).__name__}",
            code="validation_error",
        )
    return int(value)


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AuraValidationError(
            f"field {field!r} must be a string, got {type(value).__name__}",
            code="validation_error",
        )
    return value


@dataclass(frozen=True)
class SearchEvalMetrics:
    """The three ranked-retrieval metrics, each in ``[0, 1]``."""

    mrr_at_k: float
    ndcg_at_k: float
    recall_at_k: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchEvalMetrics:
        metrics = cls(
            mrr_at_k=_as_float(_require(data, "mrr_at_k", "metrics"), "mrr_at_k"),
            ndcg_at_k=_as_float(_require(data, "ndcg_at_k", "metrics"), "ndcg_at_k"),
            recall_at_k=_as_float(_require(data, "recall_at_k", "metrics"), "recall_at_k"),
        )
        for name, value in metrics.as_dict().items():
            if not 0.0 <= value <= 1.0:
                raise AuraValidationError(
                    f"metric {name!r} must be in [0, 1], got {value}", code="validation_error"
                )
        return metrics

    def as_dict(self) -> dict[str, float]:
        """Return the metrics as a plain ``name -> value`` dict."""
        return {
            "mrr_at_k": self.mrr_at_k,
            "ndcg_at_k": self.ndcg_at_k,
            "recall_at_k": self.recall_at_k,
        }


@dataclass(frozen=True)
class SearchEvalQueryResult:
    """Per-query metrics plus the ranked document ids the query returned."""

    query_id: str
    mrr_at_k: float
    ndcg_at_k: float
    recall_at_k: float
    top_docs: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchEvalQueryResult:
        top = _require(data, "top_docs", "per_query entry")
        if not isinstance(top, list) or not all(isinstance(d, str) for d in top):
            raise AuraValidationError(
                "per_query.top_docs must be a list of strings", code="validation_error"
            )
        return cls(
            query_id=_as_str(_require(data, "query_id", "per_query entry"), "query_id"),
            mrr_at_k=_as_float(_require(data, "mrr_at_k", "per_query entry"), "mrr_at_k"),
            ndcg_at_k=_as_float(_require(data, "ndcg_at_k", "per_query entry"), "ndcg_at_k"),
            recall_at_k=_as_float(_require(data, "recall_at_k", "per_query entry"), "recall_at_k"),
            top_docs=tuple(top),
        )


@dataclass(frozen=True)
class Bm25Params:
    """The effective BM25 parameters echoed by a text-bearing report."""

    k1: float
    b: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Bm25Params:
        return cls(
            k1=_as_float(_require(data, "k1", "bm25"), "k1"),
            b=_as_float(_require(data, "b", "bm25"), "b"),
        )


@dataclass(frozen=True)
class HybridWeights:
    """The hybrid fusion weights echoed by a hybrid report."""

    text: float
    vector: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HybridWeights:
        return cls(
            text=_as_float(_require(data, "text", "weights"), "text"),
            vector=_as_float(_require(data, "vector", "weights"), "vector"),
        )


@dataclass(frozen=True)
class SearchEvalReport:
    """A parsed ``auradb search eval`` relevance report.

    Covers all three modes (``bm25``, ``vector_exact``, ``hybrid``). ``bm25`` is
    present for the text-bearing modes and ``weights`` is present only for
    ``hybrid``. The metrics are dataset-specific regression signals.
    """

    dataset: str
    mode: str
    queries: int
    documents: int
    k: int
    metrics: SearchEvalMetrics
    per_query: tuple[SearchEvalQueryResult, ...]
    warnings: tuple[str, ...]
    bm25: Bm25Params | None = None
    weights: HybridWeights | None = None

    @property
    def is_hybrid(self) -> bool:
        """Whether this is a hybrid-mode report (carrying fusion weights)."""
        return self.mode == "hybrid"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SearchEvalReport:
        """Parse a report from a decoded JSON mapping.

        Raises :class:`~aura.errors.AuraValidationError` when a required field is
        missing or has the wrong shape.
        """
        if not isinstance(data, Mapping):
            raise AuraValidationError(
                "search eval report must be a JSON object", code="validation_error"
            )
        per_query_raw = _require(data, "per_query", "report")
        if not isinstance(per_query_raw, list):
            raise AuraValidationError("report.per_query must be a list", code="validation_error")
        warnings_raw = data.get("warnings", [])
        if not isinstance(warnings_raw, list) or not all(isinstance(w, str) for w in warnings_raw):
            raise AuraValidationError(
                "report.warnings must be a list of strings", code="validation_error"
            )
        bm25_raw = data.get("bm25")
        weights_raw = data.get("weights")
        return cls(
            dataset=_as_str(_require(data, "dataset", "report"), "dataset"),
            mode=_as_str(_require(data, "mode", "report"), "mode"),
            queries=_as_int(_require(data, "queries", "report"), "queries"),
            documents=_as_int(_require(data, "documents", "report"), "documents"),
            k=_as_int(_require(data, "k", "report"), "k"),
            metrics=SearchEvalMetrics.from_dict(_require(data, "metrics", "report")),
            per_query=tuple(SearchEvalQueryResult.from_dict(p) for p in per_query_raw),
            warnings=tuple(warnings_raw),
            bm25=Bm25Params.from_dict(bm25_raw) if isinstance(bm25_raw, Mapping) else None,
            weights=(
                HybridWeights.from_dict(weights_raw) if isinstance(weights_raw, Mapping) else None
            ),
        )

    @classmethod
    def from_json(cls, source: str | bytes | PathLike[str]) -> SearchEvalReport:
        """Parse a report from a JSON string/bytes or a path to a JSON file."""
        return cls.from_dict(_load_json(source))


@dataclass(frozen=True)
class ExactAnnComparisonReport:
    """A parsed ``auradb vector eval`` recall/latency report.

    This compares the approximate (HNSW preview) path against the exact baseline.
    The approximate path is a **preview, not production ANN**, and the numbers are
    dataset- and machine-specific.
    """

    collection: str
    field: str
    metric: str
    queries: int
    k: int
    ef_search: int
    mean_recall_at_k: float
    min_recall_at_k: float
    exact_latency_ms_p50: float
    ann_latency_ms_p50: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExactAnnComparisonReport:
        if not isinstance(data, Mapping):
            raise AuraValidationError(
                "vector eval report must be a JSON object", code="validation_error"
            )
        return cls(
            collection=_as_str(_require(data, "collection", "report"), "collection"),
            field=_as_str(_require(data, "field", "report"), "field"),
            metric=_as_str(_require(data, "metric", "report"), "metric"),
            queries=_as_int(_require(data, "queries", "report"), "queries"),
            k=_as_int(_require(data, "k", "report"), "k"),
            ef_search=_as_int(_require(data, "ef_search", "report"), "ef_search"),
            mean_recall_at_k=_as_float(
                _require(data, "mean_recall_at_k", "report"), "mean_recall_at_k"
            ),
            min_recall_at_k=_as_float(
                _require(data, "min_recall_at_k", "report"), "min_recall_at_k"
            ),
            exact_latency_ms_p50=_as_float(
                _require(data, "exact_latency_ms_p50", "report"), "exact_latency_ms_p50"
            ),
            ann_latency_ms_p50=_as_float(
                _require(data, "ann_latency_ms_p50", "report"), "ann_latency_ms_p50"
            ),
        )

    @classmethod
    def from_json(cls, source: str | bytes | PathLike[str]) -> ExactAnnComparisonReport:
        """Parse from a JSON string/bytes or a path to a JSON file."""
        return cls.from_dict(_load_json(source))


def _load_json(source: str | bytes | PathLike[str]) -> Any:
    """Load JSON from a path, or from a JSON string/bytes payload.

    A ``PathLike`` is always read as a file. A ``str``/``bytes`` is treated as a
    JSON payload when it parses, falling back to reading it as a file path so both
    ``from_json('{"...": ...}')`` and ``from_json('report.json')`` work.
    """
    if isinstance(source, PathLike):
        with open(source, encoding="utf-8") as handle:
            return json.load(handle)
    if isinstance(source, bytes):
        return json.loads(source)
    try:
        return json.loads(source)
    except json.JSONDecodeError:
        with open(source, encoding="utf-8") as handle:
            return json.load(handle)
