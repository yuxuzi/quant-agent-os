"""Quant Research from Python: call the research tools through the SDK and read the run back.

Usage: uv run python examples/quant-research/sdk_demo.py [STORE] ["question"]

With no store, the fixture corpus is ingested into a temporary one. Every call is recorded in
a SQLite run store next to the knowledge store, exactly as `quant-research serve-mcp` records
MCP calls, and `quant-research runs --store STORE` lists them.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import Platform
from quant_research import open_research, research_tools

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
QUESTION = "What is related to short-term reversal?"


def demo(store: Path, question: str) -> None:
    """Ask ``question`` of ``store`` through the SDK and print the cited answer and its run."""
    with (
        open_research(store, read_only=True) as research,
        Platform(
            research_tools(research.research), run_store=SqliteRunStore(store.parent / "runs.db")
        ) as platform,
    ):
        record = platform.call("find_related_signals", query=question, k=3)
        if record.output is None:
            raise SystemExit(f"{record.status}: {record.error}")
        print(f"Q: {question}")
        for entity in record.output["related"]:
            print(f"  related: {entity['name']} ({entity['type']}, score {entity['score']:.2f})")
        for passage in record.output["passages"]:
            source = passage["source"]
            print(f"  cited:   {source['source_id']}, {source['location']}")
        stored = platform.get_run(record.run_id or "")
        assert stored is not None
        print(
            f"Run {stored.run.run_id}: {stored.run.status}, "
            f"snapshot {stored.run.data_versions.get('corpus_snapshot')}, "
            f"input sha256 {stored.calls[0].input_sha256}"
        )


def main() -> None:
    """Run the demo on the given store, or on a temporary store built from the fixtures."""
    question = sys.argv[2] if len(sys.argv) > 2 else QUESTION
    if len(sys.argv) > 1:
        demo(Path(sys.argv[1]), question)
        return
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "kb.db"
        with open_research(store) as research:
            research.ingest.ingest(CORPUS)
        demo(store, question)


if __name__ == "__main__":
    main()
