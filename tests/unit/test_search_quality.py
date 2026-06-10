"""Unit tests for the search-quality report parsers."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from aura.errors import AuraValidationError
from aura.search_quality import (
    ExactAnnComparisonReport,
    SearchEvalMetrics,
    SearchEvalReport,
)


def _bm25_report() -> dict:
    return {
        "dataset": "small",
        "mode": "bm25",
        "queries": 5,
        "documents": 12,
        "k": 10,
        "preset": "default",
        "bm25": {"k1": 1.2, "b": 0.75},
        "metrics": {"mrr_at_k": 1.0, "ndcg_at_k": 0.91, "recall_at_k": 0.85},
        "per_query": [
            {
                "query_id": "q-001",
                "mrr_at_k": 1.0,
                "ndcg_at_k": 1.0,
                "recall_at_k": 1.0,
                "top_docs": ["doc-001", "doc-003"],
            }
        ],
        "warnings": [],
    }


def _hybrid_report() -> dict:
    report = _bm25_report()
    report["mode"] = "hybrid"
    report["weights"] = {"text": 0.7, "vector": 0.3}
    return report


def test_search_eval_report_parse() -> None:
    report = SearchEvalReport.from_dict(_bm25_report())
    assert report.dataset == "small"
    assert report.mode == "bm25"
    assert report.queries == 5
    assert report.documents == 12
    assert report.k == 10
    assert report.bm25 is not None
    assert report.bm25.k1 == pytest.approx(1.2)
    assert report.weights is None
    assert not report.is_hybrid
    assert len(report.per_query) == 1
    first = report.per_query[0]
    assert first.query_id == "q-001"
    assert first.top_docs == ("doc-001", "doc-003")


def test_search_eval_report_from_json_string_and_file(tmp_path) -> None:
    payload = json.dumps(_bm25_report())
    # From a JSON string.
    assert SearchEvalReport.from_json(payload).mode == "bm25"
    # From a file path.
    path = tmp_path / "report.json"
    path.write_text(payload)
    assert SearchEvalReport.from_json(path).mode == "bm25"
    assert SearchEvalReport.from_json(str(path)).mode == "bm25"


def test_search_eval_report_rejects_bad_shape() -> None:
    # Missing the required ``metrics`` block.
    bad = _bm25_report()
    del bad["metrics"]
    with pytest.raises(AuraValidationError):
        SearchEvalReport.from_dict(bad)
    # A metric outside [0, 1].
    out_of_range = _bm25_report()
    out_of_range["metrics"]["ndcg_at_k"] = 1.5
    with pytest.raises(AuraValidationError):
        SearchEvalReport.from_dict(out_of_range)
    # per_query is not a list.
    not_a_list = _bm25_report()
    not_a_list["per_query"] = {}
    with pytest.raises(AuraValidationError):
        SearchEvalReport.from_dict(not_a_list)


def test_search_eval_metrics_properties() -> None:
    metrics = SearchEvalMetrics.from_dict({"mrr_at_k": 0.8, "ndcg_at_k": 0.9, "recall_at_k": 0.7})
    assert metrics.mrr_at_k == pytest.approx(0.8)
    assert metrics.ndcg_at_k == pytest.approx(0.9)
    assert metrics.recall_at_k == pytest.approx(0.7)
    assert metrics.as_dict() == {
        "mrr_at_k": pytest.approx(0.8),
        "ndcg_at_k": pytest.approx(0.9),
        "recall_at_k": pytest.approx(0.7),
    }
    # Frozen value object.
    with pytest.raises(FrozenInstanceError):
        metrics.mrr_at_k = 0.0  # type: ignore[misc]


def test_hybrid_report_parse() -> None:
    report = SearchEvalReport.from_dict(_hybrid_report())
    assert report.is_hybrid
    assert report.weights is not None
    assert report.weights.text == pytest.approx(0.7)
    assert report.weights.vector == pytest.approx(0.3)
    # Hybrid still carries BM25 params.
    assert report.bm25 is not None


def test_exact_ann_comparison_report_parse() -> None:
    report = ExactAnnComparisonReport.from_dict(
        {
            "collection": "Doc",
            "field": "embedding",
            "metric": "cosine",
            "queries": 10,
            "k": 10,
            "ef_search": 64,
            "mean_recall_at_k": 0.97,
            "min_recall_at_k": 0.9,
            "exact_latency_ms_p50": 0.4,
            "ann_latency_ms_p50": 0.2,
        }
    )
    assert report.collection == "Doc"
    assert report.ef_search == 64
    assert report.mean_recall_at_k == pytest.approx(0.97)


def test_exact_ann_comparison_report_rejects_bad_shape() -> None:
    with pytest.raises(AuraValidationError):
        ExactAnnComparisonReport.from_dict({"collection": "Doc"})
