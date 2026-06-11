"""Parse AuraDB analyzer-aware ``search eval`` reports into typed objects.

AuraDB v1.5.0 adds query-time analyzer presets to the ``auradb search eval`` CLI
(``--analyzer`` and ``search eval compare-analyzers``). The connector does **not**
run the CLI or compute relevance — these helpers parse the JSON the CLI produced,
which is handy when a pipeline sweeps analyzers and asserts on the numbers.

The metrics are dataset-specific regression signals, not universal benchmarks.

Run: ``python examples/auradb_search_eval_analyzers.py``
"""

from __future__ import annotations

import json

from aura import AnalyzerComparisonReport, SearchEvalReport

# A single-analyzer report (what `auradb search eval --analyzer ascii_fold` emits).
_SINGLE = json.dumps(
    {
        "dataset": "analyzer",
        "mode": "bm25",
        "analyzer": "ascii_fold",
        "queries": 6,
        "documents": 8,
        "k": 10,
        "bm25": {"k1": 1.2, "b": 0.75},
        "metrics": {"mrr_at_k": 0.92, "ndcg_at_k": 0.92, "recall_at_k": 1.0},
        "per_query": [],
        "warnings": [],
    }
)

# A comparison report (what `auradb search eval compare-analyzers` emits).
_COMPARE = json.dumps(
    {
        "dataset": "analyzer",
        "mode": "bm25",
        "k": 10,
        "analyzers": [
            {
                "analyzer": "default",
                "metrics": {"mrr_at_k": 0.75, "ndcg_at_k": 0.74, "recall_at_k": 0.75},
                "warnings": [],
            },
            {
                "analyzer": "simple",
                "metrics": {"mrr_at_k": 0.75, "ndcg_at_k": 0.74, "recall_at_k": 0.75},
                "warnings": [],
            },
            {
                "analyzer": "ascii_fold",
                "metrics": {"mrr_at_k": 0.92, "ndcg_at_k": 0.92, "recall_at_k": 1.0},
                "warnings": [],
            },
            {
                "analyzer": "keyword",
                "metrics": {"mrr_at_k": 0.17, "ndcg_at_k": 0.17, "recall_at_k": 0.17},
                "warnings": [],
            },
        ],
    }
)


def main() -> None:
    single = SearchEvalReport.from_json(_SINGLE)
    print(f"single report analyzer={single.analyzer} recall={single.metrics.recall_at_k}")

    report = AnalyzerComparisonReport.from_json(_COMPARE)
    print(f"comparison over {report.dataset} (mode={report.mode}, k={report.k}):")
    for leg in report.analyzers:
        print(f"  {leg.analyzer:>10}: recall@k={leg.metrics.recall_at_k}")

    # `default` and `simple` tokenize identically, so they score the same here.
    assert report.metrics_for("default") == report.metrics_for("simple")
    # ascii_fold recovers accented matches the unfolded analyzers miss.
    assert report.metrics_for("ascii_fold").recall_at_k > report.metrics_for("simple").recall_at_k
    print("default == simple; ascii_fold recovers accents (fixture-specific signal)")


if __name__ == "__main__":
    main()
