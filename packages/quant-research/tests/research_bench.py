"""Opt-in scale check for Quant Research on the mock world (plan G5). Not a test.

Usage: uv run python packages/quant-research/tests/research_bench.py [COPIES]

It renders the mock world COPIES times (default 50, i.e. 300 documents, a third of them PDFs),
ingests it into a temporary SQLite store with and without the hashing vector channel, and
reports ingestion time and query latency (p50/p95) for one- and two-hop questions. Timings vary
by machine; compare runs on the same machine only.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

from mock_world import QUESTIONS, write_world

from quant_research import ResearchQuery, open_research

REPEATS = 5


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def bench(copies: int, *, vector: bool) -> dict[str, float]:
    """Return ingestion seconds, store counts, and query latency in milliseconds."""
    with tempfile.TemporaryDirectory() as tmp:
        corpus = write_world(Path(tmp) / "corpus", copies=copies)
        with open_research(Path(tmp) / "kb.db", vector=vector) as research:
            started = time.perf_counter()
            result = research.ingest.ingest(corpus)
            ingest_seconds = time.perf_counter() - started
            latencies: dict[int, list[float]] = {1: [], 2: []}
            for _ in range(REPEATS):
                for question in QUESTIONS:
                    for hops in (1, 2):
                        query = ResearchQuery(
                            query=question.query,
                            entity_types=list(question.entity_types),  # type: ignore[arg-type]
                            hops=hops,
                        )
                        started = time.perf_counter()
                        research.research.find_related(query)
                        latencies[hops].append((time.perf_counter() - started) * 1000)
    return {
        "documents": len(result.added),
        "chunks": result.chunks,
        "relations": result.relations,
        "ingest_seconds": round(ingest_seconds, 2),
        "p50_ms_1_hop": round(statistics.median(latencies[1]), 1),
        "p95_ms_1_hop": round(_percentile(latencies[1], 0.95), 1),
        "p50_ms_2_hops": round(statistics.median(latencies[2]), 1),
        "p95_ms_2_hops": round(_percentile(latencies[2], 0.95), 1),
    }


if __name__ == "__main__":
    copies = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    for vector in (False, True):
        print(
            ("hashing vectors" if vector else "no vectors").ljust(16), bench(copies, vector=vector)
        )
