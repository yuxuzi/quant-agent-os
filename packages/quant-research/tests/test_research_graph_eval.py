"""Graph RAG quality must not regress below the recorded baseline (plan G2)."""

import json

import pytest
from graph_eval import BASELINE, LOWER_IS_BETTER, headline, run

METRICS = headline(run())
BASELINE_METRICS: dict[str, float] = json.loads(BASELINE.read_text("utf-8"))


@pytest.mark.parametrize("metric", sorted(BASELINE_METRICS))
def test_graph_metric_does_not_regress(metric: str) -> None:
    current, recorded = METRICS[metric], BASELINE_METRICS[metric]
    if metric in LOWER_IS_BETTER:
        assert current <= recorded, f"{metric} rose from {recorded} to {current}"
    else:
        assert current >= recorded - 1e-9, f"{metric} fell from {recorded} to {current}"


def test_unanswerable_questions_stay_empty() -> None:
    assert METRICS["empty_query_accuracy"] == 1.0
