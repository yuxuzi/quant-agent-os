"""Retrieval quality must not regress below the recorded baseline (plan step R2)."""

import json

import pytest
from research_eval import BASELINE, MUST_BE_PERFECT, run

METRICS = run()
BASELINE_METRICS: dict[str, float] = json.loads(BASELINE.read_text("utf-8"))


@pytest.mark.parametrize("metric", sorted(BASELINE_METRICS))
def test_metric_does_not_regress(metric: str) -> None:
    assert METRICS[metric] >= BASELINE_METRICS[metric] - 1e-9, (
        f"{metric} fell from {BASELINE_METRICS[metric]} to {METRICS[metric]}"
    )


@pytest.mark.parametrize("metric", MUST_BE_PERFECT)
def test_every_citation_and_location_is_valid(metric: str) -> None:
    assert METRICS[metric] == 1.0


def test_the_vector_channel_never_makes_retrieval_worse() -> None:
    with_vectors = run(vector=True)

    assert all(with_vectors[metric] >= METRICS[metric] - 1e-9 for metric in METRICS), with_vectors
