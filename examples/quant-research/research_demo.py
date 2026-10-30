"""Quant Research end to end, offline: ingest a corpus, then query it as an MCP client would.

Usage: uv run python examples/quant-research/research_demo.py [CORPUS] ["question"]

The default corpus is the synthetic fixture corpus. The store is temporary, so every run starts
clean. Tool calls go through `quant_agent_os.mcp`, the adapter an MCP server transport uses.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from quant_agent_os.mcp import call_tool, tool_definitions
from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.reporting import render_markdown
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.runtime.orchestrator import Orchestrator
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_research import open_research, research_tools

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
DEFAULT_QUESTION = "What is related to short-term reversal?"


def main() -> None:
    """Ingest, list the MCP tools, answer one question with citations, and print a report."""
    corpus = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS
    question = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUESTION
    with tempfile.TemporaryDirectory() as tmp, open_research(Path(tmp) / "kb.db") as research:
        ingested = research.ingest.ingest(corpus)  # operator step: not an agent tool
        print(f"Ingested {len(ingested.added)} documents -> snapshot {ingested.snapshot.id}\n")

        registry = ToolRegistry(research_tools(research.research))
        executor = ToolExecutor(registry, PermissionPolicy())
        print("MCP tools:", ", ".join(tool["name"] for tool in tool_definitions(executor)))

        result = call_tool(executor, "find_related_signals", {"query": question})
        packet: dict[str, Any] = result["structuredContent"]
        print(f"\n{question}")
        for entity in packet["related"]:
            print(f"  - {entity['name']} ({entity['type']}, score {entity['score']:.2f})")
        print("Cited passages:")
        for passage in packet["passages"]:
            source = passage["source"]
            print(f"  [{passage['chunk_id']}] {source['source_id']}, {source['location']}")

        plan = {
            "goal": question,
            "steps": [{"tool": "find_related_signals", "arguments": {"query": question}}],
        }
        orchestrator = Orchestrator(registry, FakeModelProvider([json.dumps(plan)]))
        print("\n" + render_markdown(orchestrator.run(question)))


if __name__ == "__main__":
    main()
