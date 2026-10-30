"""Retrieval-quality evaluation over the fixture corpus (plan step R2).

Run ``uv run python packages/quant-research/tests/research_eval.py`` to print the metrics, and
add ``--update`` to rewrite the recorded baseline after an intended improvement.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from quant_research import ResearchApp, ResearchQuery, open_research
from quant_research.domain.vocabulary import EntityMatcher

HERE = Path(__file__).parent
CORPUS = HERE / "fixtures" / "corpus"
GOLD = HERE / "evaluation" / "gold.toml"
BASELINE = HERE / "evaluation" / "baseline.json"
MUST_BE_PERFECT = ("citation_validity", "location_validity")


def evaluate(research: ResearchApp) -> dict[str, float]:
    """Return mean related recall, passage precision, validity rates, and empty-query accuracy."""
    queries = tomllib.loads(GOLD.read_text("utf-8"))["queries"]
    matcher = EntityMatcher(research.vocabulary)
    recall: list[float] = []
    precision: list[float] = []
    empty: list[float] = []
    citations: list[bool] = []
    locations: list[bool] = []
    for item in queries:
        packet = research.research.find_related(ResearchQuery(query=item["query"]))
        if item.get("expect_empty"):
            empty.append(float(not packet.passages))
            continue
        found = {entity.id for entity in packet.related}
        recall.append(len(found & set(item["related"])) / len(item["related"]))
        relevant = [p.source.source_id in item["documents"] for p in packet.passages]
        precision.append(sum(relevant) / len(relevant) if relevant else 0.0)
        for entity in packet.related:
            for link in entity.links:
                chunk = research.store.get_chunk(link.chunk_id)
                mentioned = {m.entity_id for m in matcher.find(chunk.text)} if chunk else set()
                citations.append({link.subject, link.object} <= mentioned)
        for passage in packet.passages:
            locations.append(_location_matches(research, passage.chunk_id))
    return {
        "related_recall": _mean(recall),
        "passage_precision": _mean(precision),
        "citation_validity": _mean([float(ok) for ok in citations]),
        "location_validity": _mean([float(ok) for ok in locations]),
        "empty_query_accuracy": _mean(empty),
    }


def _location_matches(research: ResearchApp, chunk_id: str) -> bool:
    chunk = research.store.get_chunk(chunk_id)
    document = research.store.get_document(chunk.document_id) if chunk else None
    if chunk is None or document is None:
        return False
    lines = (CORPUS / document.uri).read_text("utf-8").splitlines()
    return "\n".join(lines[chunk.line_start - 1 : chunk.line_end]).strip() == chunk.text


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 1.0


def run() -> dict[str, float]:
    """Ingest the fixture corpus into a fresh store and evaluate it."""
    with open_research(":memory:") as research:
        research.ingest.ingest(CORPUS)
        return evaluate(research)


if __name__ == "__main__":
    metrics = run()
    print(json.dumps(metrics, indent=2))
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"Baseline written to {BASELINE}.")
