"""Graph RAG evaluation against the mock world's answer key (plan G2).

Run ``uv run python packages/quant-research/tests/graph_eval.py`` to print the metrics and the
per-predicate and per-question breakdown, and add ``--update`` to rewrite
``evaluation/graph_baseline.json`` after an intended improvement.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mock_world import QUESTIONS, key, negations, true_triples, write_world

from quant_research import ResearchQuery, open_research
from quant_research.application.retrieval import ALL_CHANNELS, ResearchService

HERE = Path(__file__).parent
BASELINE = HERE / "evaluation" / "graph_baseline.json"
K = 3  # few passages per answer, so channel quality shows (6 documents in the world)


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def extraction_metrics(relations: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Score typed extracted triples (``related_to`` excluded) against the answer key."""
    truth = true_triples()
    predicted = {key(s, p, o) for s, p, o in relations if p != "related_to"}
    hits = predicted & truth
    negated = {frozenset((n.first, n.second)) for n in negations()}
    per_predicate: dict[str, dict[str, float]] = {}
    for predicate in sorted({p for _, p, _ in truth | predicted}):
        t = {x for x in truth if x[1] == predicate}
        g = {x for x in predicted if x[1] == predicate}
        precision = len(t & g) / len(g) if g else 0.0
        recall = len(t & g) / len(t) if t else 0.0
        per_predicate[predicate] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "true": len(t),
            "extracted": len(g),
        }
    precision = len(hits) / len(predicted) if predicted else 0.0
    recall = len(hits) / len(truth)
    return {
        "extraction_precision": round(precision, 4),
        "extraction_recall": round(recall, 4),
        "extraction_f1": _f1(precision, recall),
        "negation_false_positives": sum(frozenset((s, o)) in negated for s, _, o in predicted),
        "direction_errors": sum(
            (o, p, s) in truth and (s, p, o) not in truth for s, p, o in predicted
        ),
        "per_predicate": per_predicate,
        "missed": sorted(truth - predicted),
        "spurious": sorted(predicted - truth),
    }


def _path_ids(entity: Any, seeds: set[str]) -> tuple[str, ...] | None:
    """Return the entity ids along ``entity.path`` from its seed, or None without a path."""
    path = getattr(entity, "path", None)
    if not path:
        return None
    ids = [path[0].subject if path[0].subject in seeds else path[0].object]
    for link in path:
        ids.append(link.object if link.subject == ids[-1] else link.subject)
    return tuple(ids)


def answer_metrics(service: ResearchService) -> dict[str, Any]:
    """Score related-entity recall, evidence recall@k, paths, and unanswerable questions."""
    recall: list[float] = []
    evidence: list[float] = []
    precision: list[float] = []
    multi_hop_evidence: list[float] = []
    path_valid: list[float] = []
    path_expected: list[float] = []
    empty: list[float] = []
    detail: dict[str, Any] = {}
    for question in QUESTIONS:
        packet = service.find_related(
            ResearchQuery(
                query=question.query,
                entity_types=list(question.entity_types),  # type: ignore[arg-type]
                hops=question.hops,
                k=K,
            )
        )
        if not question.expected:
            empty.append(float(not packet.passages and not packet.related))
            continue
        found = {entity.id for entity in packet.related}
        docs = {passage.source.source_id for passage in packet.passages}
        recall.append(len(found & question.expected) / len(question.expected))
        doc_recall = len(docs & question.documents) / len(question.documents)
        evidence.append(doc_recall)
        relevant = [p.source.source_id in question.documents for p in packet.passages]
        precision.append(sum(relevant) / len(relevant) if relevant else 0.0)
        if question.hops > 1:
            multi_hop_evidence.append(doc_recall)
        seeds = set(packet.seed_entities)
        for entity in packet.related:
            if entity.hops == 0:
                continue
            ids = _path_ids(entity, seeds)
            path_valid.append(float(ids is not None and ids[0] in seeds and ids[-1] == entity.id))
            if entity.id in question.paths:
                path_expected.append(float(ids == question.paths[entity.id]))
        detail[question.query] = {
            "missing": sorted(question.expected - found),
            "evidence_missing": sorted(question.documents - docs),
        }
    return {
        "related_recall": _mean(recall),
        "evidence_recall": _mean(evidence),
        "evidence_precision": _mean(precision),
        "multi_hop_evidence_recall": _mean(multi_hop_evidence),
        "path_validity": _mean(path_valid),
        "expected_path_accuracy": _mean(path_expected),
        "empty_query_accuracy": _mean(empty),
        "questions": detail,
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def run() -> dict[str, Any]:
    """Build the mock world, ingest it, and return every metric plus the ablation."""
    with tempfile.TemporaryDirectory() as tmp, open_research(":memory:") as research:
        research.ingest.ingest(write_world(Path(tmp)))
        triples = [(r.subject_id, r.predicate, r.object_id) for r in research.store.all_relations()]
        extraction = extraction_metrics(triples)
        answers = answer_metrics(research.research)
        no_graph = ResearchService(
            research.store, research.vocabulary, channels=ALL_CHANNELS - {"graph"}
        )
        ablation = answer_metrics(no_graph)
    return {
        **{k: v for k, v in extraction.items() if not isinstance(v, (dict, list))},
        **{k: v for k, v in answers.items() if not isinstance(v, dict)},
        "evidence_recall_without_graph": ablation["evidence_recall"],
        "evidence_precision_without_graph": ablation["evidence_precision"],
        "multi_hop_evidence_recall_without_graph": ablation["multi_hop_evidence_recall"],
        "_detail": {
            "per_predicate": extraction["per_predicate"],
            "missed": extraction["missed"],
            "spurious": extraction["spurious"],
            "questions": answers["questions"],
        },
    }


# Metrics where lower is better; everything else must not fall below the baseline.
LOWER_IS_BETTER = ("negation_false_positives", "direction_errors")


def headline(metrics: dict[str, Any]) -> dict[str, float]:
    """Return the scalar metrics that the baseline records."""
    return {k: v for k, v in metrics.items() if not k.startswith("_")}


if __name__ == "__main__":
    metrics = run()
    print(json.dumps(metrics, indent=2, default=list))
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(headline(metrics), indent=2, sort_keys=True) + "\n")
        print(f"Baseline written to {BASELINE}.")
