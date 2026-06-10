"""Read an AuraDB ``search eval`` relevance report into typed objects.

AuraDB's ``auradb search eval`` CLI (v1.4.0) measures ranked-retrieval relevance
(MRR@k / NDCG@k / Recall@k) on a relevance dataset and prints a JSON report. The
connector does **not** run that command or compute relevance itself — these
helpers just parse the report the CLI produced, which is handy when a deployment
pipeline runs the CLI and wants to assert on the numbers.

The metrics are dataset-specific regression signals, not universal benchmarks.

Run: ``python examples/auradb_search_eval_report.py``
"""

from __future__ import annotations

import json

from aura.search_quality import SearchEvalReport

# A representative report payload (what `auradb search eval --mode hybrid` emits).
# In practice you would read this from a file the CLI wrote.
_REPORT = json.dumps(
    {
        "dataset": "small",
        "mode": "hybrid",
        "queries": 5,
        "documents": 12,
        "k": 10,
        "bm25": {"k1": 1.2, "b": 0.75},
        "weights": {"text": 0.7, "vector": 0.3},
        "metrics": {"mrr_at_k": 1.0, "ndcg_at_k": 0.99, "recall_at_k": 1.0},
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
)


def main() -> None:
    report = SearchEvalReport.from_json(_REPORT)  # also accepts a file path
    print(f"dataset={report.dataset} mode={report.mode} k={report.k}")
    print(f"NDCG@{report.k}: {report.metrics.ndcg_at_k:.3f}")
    print(f"Recall@{report.k}: {report.metrics.recall_at_k:.3f}")

    if report.is_hybrid and report.weights is not None:
        print(f"fusion weights: text={report.weights.text} vector={report.weights.vector}")
    if report.bm25 is not None:
        print(f"bm25: k1={report.bm25.k1} b={report.bm25.b}")

    # A pipeline can branch on a per-dataset regression threshold. The bands are
    # fixture-specific — pick them for your own dataset.
    threshold = 0.9
    if report.metrics.ndcg_at_k < threshold:
        print(f"WARNING: NDCG below {threshold} for dataset {report.dataset!r}")
    else:
        print("relevance within expected band")

    for q in report.per_query:
        print(f"  {q.query_id}: ndcg={q.ndcg_at_k:.3f} top={list(q.top_docs)}")


if __name__ == "__main__":
    main()
