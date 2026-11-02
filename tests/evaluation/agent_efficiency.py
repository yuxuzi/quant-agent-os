"""Agent-efficiency evaluation: what a client pays, in context and calls, to do typical tasks.

A scripted client drives the MCP servers the products serve, over an in-memory MCP session, the
way a well-behaved agent would. For each task it records the calls made, the bytes the model
must write as arguments, the bytes returned into its context, and whether the task succeeded.
For each server it records the bytes of tool definitions and instructions the model sees on
every turn: name, description, and input schema. The title, output schema, and annotations are
for the client and are counted apart. Bytes stand in for tokens, so the numbers do not depend on
a model. The client validates every result against its tool's output schema.

Run ``uv run python tests/evaluation/agent_efficiency.py`` to print the metrics, and add
``--update`` to rewrite the recorded baseline after an intended change. Offline, deterministic,
and no model involved (plan step P1.1 in docs/plans/agent-surface.md).
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from quant_agent_os.mcp import build_server
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry, ToolSpec
from quant_analytics.tools import analytics_tools
from quant_app_factory import AppFactory, PytemplateUvScaffolder
from quant_app_factory.cli import MCP_INSTRUCTIONS as FACTORY_INSTRUCTIONS
from quant_app_factory.cli import served_tools as factory_served_tools
from quant_research import open_research
from quant_research.cli import MCP_INSTRUCTIONS as RESEARCH_INSTRUCTIONS
from quant_research.tools import research_tools

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
BASELINE = Path(__file__).parent / "agent_efficiency_baseline.json"
ANALYTICS_INSTRUCTIONS = (
    "Quant Analytics computes performance and risk metrics with stated conventions. "
    "Report its numbers as computed; do not recompute them."
)
WORKDIR_PLACEHOLDER = "/work"
# What a client sends the model for each tool (the Messages API tool format); the title is for
# display and the output schema for validating results, so both are counted as client-only.
MODEL_VISIBLE = ("name", "description", "inputSchema")
CLIENT_ONLY = ("title", "outputSchema", "annotations")


def daily_returns(seed: int, periods: int = 252) -> list[float]:
    """Deterministic, plausible daily returns rounded as an agent would send them."""
    return [
        round(0.0004 + 0.01 * math.sin(seed * 1.7 + day * 0.9) * math.cos(day * 0.13 + seed), 4)
        for day in range(periods)
    ]


@dataclass
class TaskResult:
    """One task's cost and outcome."""

    calls: int = 0
    argument_bytes: int = 0
    result_bytes: int = 0
    success: bool = False
    errors: list[str] = field(default_factory=list)


class Client:
    """Wraps a session and meters every call.

    ``workdir`` is a real scratch directory for tools that need one; it is metered as the fixed
    `WORKDIR_PLACEHOLDER`, so byte counts do not depend on where the temporary directory is.
    """

    def __init__(self, session: ClientSession, result: TaskResult, workdir: str) -> None:
        self.session = session
        self.result = result
        self.workdir = workdir

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call ``tool``, meter it, and return its structured content (or the error text)."""
        self.result.calls += 1
        self.result.argument_bytes += self._metered(json.dumps(arguments, separators=(",", ":")))
        reply = await self.session.call_tool(tool, arguments)
        self.result.result_bytes += sum(
            self._metered(item.text) for item in reply.content if item.type == "text"
        )
        if reply.isError:
            self.result.errors.append(f"{tool}: {reply.content[0].text}")  # type: ignore[union-attr]
            return {}
        return reply.structuredContent or {}

    def _metered(self, text: str) -> int:
        return len(text.replace(self.workdir, WORKDIR_PLACEHOLDER).encode("utf-8"))


Task = Callable[[Client], Awaitable[bool]]


async def search_and_cite(client: Client) -> bool:
    """Find what relates to a signal, then read the top passage in full to quote it."""
    packet = await client.call(
        "find_related_signals", {"query": "What is related to short-term reversal?"}
    )
    passages = packet.get("passages", [])
    if not passages:
        return False
    passage = await client.call("get_source_passage", {"chunk_id": passages[0]["chunk_id"]})
    return bool(passage.get("text")) and passage.get("chunk_id") == passages[0]["chunk_id"]


async def draft_strategy(client: Client) -> bool:
    """Draft a cited strategy specification for one signal."""
    draft = await client.call("generate_strategy", {"focus": "momentum"})
    return bool(draft) and not client.result.errors


async def analyze_a_series(client: Client) -> bool:
    """Analyse a year of daily returns against a benchmark."""
    output = await client.call(
        "analyze_performance",
        {
            "series_id": "fund",
            "returns": daily_returns(1),
            "benchmark": daily_returns(2),
            "periods_per_year": 252,
        },
    )
    metrics = output.get("metrics", {})
    return metrics.get("sharpe_ratio") is not None and metrics.get("beta") is not None


async def analyze_portfolio_risk(client: Client) -> bool:
    """Decompose a three-asset portfolio's volatility over a year of daily returns."""
    assets = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}
    output = await client.call(
        "analyze_risk",
        {
            "portfolio_id": "book",
            "weights": assets,
            "asset_returns": {name: daily_returns(seed) for seed, name in enumerate(assets, 3)},
            "periods_per_year": 252,
        },
    )
    return set(output.get("contributions", {})) == set(assets)


async def check_an_app_spec(client: Client) -> bool:
    """Learn what a template asks, then check a draft spec until it is ready."""
    described = await client.call(
        "describe_template", {"template": "quant-baseline", "profile": "quant-research"}
    )
    if not described:
        return False
    checked = await client.call(
        "check_spec",
        {
            "spec": {
                "name": "momo-lab",
                "kind": "quant-research",
                "description": "Momentum research",
                "output_dir": client.workdir,
            }
        },
    )
    return bool(checked.get("check", {}).get("ready"))


TASKS: dict[str, tuple[str, Task]] = {
    "search_and_cite": ("research", search_and_cite),
    "draft_strategy": ("research", draft_strategy),
    "analyze_a_series": ("analytics", analyze_a_series),
    "analyze_portfolio_risk": ("analytics", analyze_portfolio_risk),
    "check_an_app_spec": ("factory", check_an_app_spec),
}


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


async def _evaluate(
    servers: dict[str, tuple[list[ToolSpec[Any, Any]], str]], workdir: str
) -> dict[str, Any]:
    definitions: dict[str, dict[str, int]] = {}
    tasks: dict[str, dict[str, Any]] = {}
    for name, (specs, instructions) in servers.items():
        executor = ToolExecutor(ToolRegistry(specs), PermissionPolicy())
        server = build_server(executor, name=name, instructions=instructions)
        async with create_connected_server_and_client_session(server) as session:
            listed = [
                tool.model_dump(mode="json", exclude_none=True)
                for tool in (await session.list_tools()).tools
            ]
            definitions[name] = {
                "tools": len(listed),
                "definition_bytes": sum(
                    _size({key: tool[key] for key in MODEL_VISIBLE if key in tool})
                    for tool in listed
                ),
                "instruction_bytes": len(instructions.encode("utf-8")),
                "client_only_bytes": sum(
                    _size({key: tool[key] for key in CLIENT_ONLY if key in tool}) for tool in listed
                ),
            }
            for task_name, (server_name, task) in TASKS.items():
                if server_name != name:
                    continue
                result = TaskResult()
                result.success = await task(Client(session, result, workdir))
                tasks[task_name] = {
                    "calls": result.calls,
                    "argument_bytes": result.argument_bytes,
                    "result_bytes": result.result_bytes,
                    "success": result.success,
                }
                if result.errors:
                    tasks[task_name]["errors"] = result.errors
    totals = {
        "definition_bytes": sum(item["definition_bytes"] for item in definitions.values()),
        "instruction_bytes": sum(item["instruction_bytes"] for item in definitions.values()),
        "tools": sum(item["tools"] for item in definitions.values()),
        "calls": sum(item["calls"] for item in tasks.values()),
        "argument_bytes": sum(item["argument_bytes"] for item in tasks.values()),
        "result_bytes": sum(item["result_bytes"] for item in tasks.values()),
        "success_rate": sum(item["success"] for item in tasks.values()) / len(tasks),
    }
    return {"servers": definitions, "tasks": tasks, "totals": totals}


def run() -> dict[str, Any]:
    """Build each product's MCP server as its CLI serves it, run every task, return metrics."""
    research = open_research(":memory:")
    workdir = tempfile.TemporaryDirectory()
    try:
        research.ingest.ingest(CORPUS)
        servers = {
            "research": (research_tools(research.research), RESEARCH_INSTRUCTIONS),
            "analytics": (analytics_tools().specs(), ANALYTICS_INSTRUCTIONS),
            "factory": (
                factory_served_tools(AppFactory(PytemplateUvScaffolder())),
                FACTORY_INSTRUCTIONS,
            ),
        }
        return anyio.run(_evaluate, servers, workdir.name)
    finally:
        research.close()
        workdir.cleanup()


if __name__ == "__main__":
    metrics = run()
    print(json.dumps(metrics, indent=2))
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"Baseline written to {BASELINE.relative_to(ROOT)}")
