"""Agent-efficiency evaluation: what a client pays, in context and calls, to do typical tasks.

A scripted client drives the platform server (every toolset, as `quant-platform serve-mcp`
serves it) over an in-memory MCP session, the way a well-behaved agent would: it names
datasets rather than copying series, and uses `research_report` for a report. For each task
it records the calls made, the bytes the model must write as arguments, the bytes returned
into its context, and whether the task succeeded.
For the server it records the bytes of tool definitions and instructions the model sees on
every turn: name, description, and input schema. The title, output schema, and annotations are
for the client and are counted apart. Bytes stand in for tokens, so the numbers do not depend on
a model. The client validates every result against its tool's output schema.

The skills (plan step P3) are counted too. A client keeps every skill's description in context
and loads one skill's full text when a task needs it, so each task records the bytes of the
skill it would load (`SKILL_FOR_TASK`) and the tools it called. The client is scripted, so the
skills cannot change its calls; the test instead checks that each task calls only tools its
skill teaches, so the scripted workflow and the skill's stay the same.

Run ``uv run python tests/evaluation/agent_efficiency.py`` to print the metrics, and add
``--update`` to rewrite the recorded baseline after an intended change. Offline, deterministic,
and no model involved (plan steps P1.1 and P2 in docs/plans/agent-surface.md).
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import build_server
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_app_factory import AppFactory, PytemplateUvScaffolder
from quant_platform import Catalog, compose
from quant_research import open_research

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "research" / "fixtures" / "corpus"
BASELINE = Path(__file__).parent / "agent_efficiency_baseline.json"
WORKDIR_PLACEHOLDER = "/work"
# What a client sends the model for each tool (the Messages API tool format); the title is for
# display and the output schema for validating results, so both are counted as client-only.
MODEL_VISIBLE = ("name", "description", "inputSchema")
CLIENT_ONLY = ("title", "outputSchema", "annotations")
SKILLS = ROOT / "skills"
SKILL_FOR_TASK = {
    "search_and_cite": "quant-research",
    "draft_strategy": "quant-strategy",
    "analyze_a_series": "quant-analytics",
    "analyze_portfolio_risk": "quant-analytics",
    "report_on_a_signal": "quant-strategy",
    "check_an_app_spec": "quant-app-factory",
}


ASSETS = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}


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
    tools: list[str] = field(default_factory=list)


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
        self.result.tools.append(tool)
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
        # JSON escapes the backslashes of a Windows path, so replace the escaped form first.
        escaped = json.dumps(self.workdir)[1:-1]
        text = text.replace(escaped, WORKDIR_PLACEHOLDER).replace(self.workdir, WORKDIR_PLACEHOLDER)
        return len(text.encode("utf-8"))


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
    report = await client.call("research_report", {"focus": "momentum"})
    return bool(report.get("spec")) and not client.result.errors


async def analyze_a_series(client: Client) -> bool:
    """Find the dataset, then analyse a year of daily returns against a benchmark by reference."""
    listed = await client.call("list_datasets", {})
    if not listed.get("datasets"):
        return False
    output = await client.call(
        "analyze_performance",
        {
            "returns": {"dataset": "daily", "column": "fund"},
            "benchmark": {"dataset": "daily", "column": "benchmark"},
        },
    )
    metrics = output.get("metrics", {})
    return metrics.get("sharpe_ratio") is not None and metrics.get("beta") is not None


async def analyze_portfolio_risk(client: Client) -> bool:
    """Find the dataset, then decompose a three-asset portfolio's volatility by reference."""
    listed = await client.call("list_datasets", {"dataset_id": "daily"})
    if not listed.get("datasets"):
        return False
    output = await client.call(
        "analyze_risk",
        {
            "portfolio_id": "book",
            "weights": ASSETS,
            "asset_returns": {"dataset": "daily"},
        },
    )
    return set(output.get("contributions", {})) == set(ASSETS)


async def report_on_a_signal(client: Client) -> bool:
    """Get a cited report on a signal, with the metrics of a strategy's returns, in one call."""
    report = await client.call(
        "research_report",
        {
            "focus": "momentum",
            "returns": {"dataset": "daily", "column": "fund"},
        },
    )
    kinds = {item["kind"] for item in report.get("evidence", [])}
    return kinds == {"retrieved", "computed"} and bool(report.get("metrics"))


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


TASKS: dict[str, Task] = {
    "search_and_cite": search_and_cite,
    "draft_strategy": draft_strategy,
    "analyze_a_series": analyze_a_series,
    "analyze_portfolio_risk": analyze_portfolio_risk,
    "report_on_a_signal": report_on_a_signal,
    "check_an_app_spec": check_an_app_spec,
}


def skill_metrics() -> dict[str, dict[str, int]]:
    """Return each skill's description bytes (always in context) and full size (on demand)."""
    metrics: dict[str, dict[str, int]] = {}
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        text = path.read_text("utf-8")
        description = next(
            line for line in text.splitlines() if line.startswith("description: ")
        ).removeprefix("description: ")
        metrics[path.parent.name] = {
            "description_bytes": len(description.encode("utf-8")),
            "skill_bytes": len(text.encode("utf-8")),
        }
    return metrics


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


async def _definitions(catalog: Catalog) -> dict[str, int]:
    """Return what a client is offered by a server for ``catalog``, as bytes per turn."""
    server = build_server(
        ToolExecutor(ToolRegistry(catalog.tools), PermissionPolicy()),
        name="quant-agent-os",
        instructions=catalog.instructions,
        resources=catalog.resources,
        prompts=catalog.prompts,
    )
    async with create_connected_server_and_client_session(server) as session:
        listed = [
            tool.model_dump(mode="json", exclude_none=True)
            for tool in (await session.list_tools()).tools
        ]
    return {
        "tools": len(listed),
        "definition_bytes": sum(
            _size({key: tool[key] for key in MODEL_VISIBLE if key in tool}) for tool in listed
        ),
        "instruction_bytes": len(catalog.instructions.encode("utf-8")),
        "client_only_bytes": sum(
            _size({key: tool[key] for key in CLIENT_ONLY if key in tool}) for tool in listed
        ),
    }


async def _evaluate(default: Catalog, everything: Catalog, workdir: str) -> dict[str, Any]:
    """Measure the default server's offer and the one with App Factory, and run every task."""
    definitions = await _definitions(default)
    with_factory = await _definitions(everything)
    server = build_server(
        ToolExecutor(ToolRegistry(everything.tools), PermissionPolicy()),
        name="quant-agent-os",
        instructions=everything.instructions,
        resources=everything.resources,
        prompts=everything.prompts,
    )
    tasks: dict[str, dict[str, Any]] = {}
    async with create_connected_server_and_client_session(server) as session:
        skills = skill_metrics()
        for task_name, task in TASKS.items():
            result = TaskResult()
            result.success = await task(Client(session, result, workdir))
            tasks[task_name] = {
                "calls": result.calls,
                "tools": sorted(set(result.tools)),
                "skill": SKILL_FOR_TASK[task_name],
                "skill_bytes": skills[SKILL_FOR_TASK[task_name]]["skill_bytes"],
                "argument_bytes": result.argument_bytes,
                "result_bytes": result.result_bytes,
                "success": result.success,
            }
            if result.errors:
                tasks[task_name]["errors"] = result.errors
    totals = {
        "definition_bytes": definitions["definition_bytes"],
        "instruction_bytes": definitions["instruction_bytes"],
        "tools": definitions["tools"],
        "calls": sum(item["calls"] for item in tasks.values()),
        "argument_bytes": sum(item["argument_bytes"] for item in tasks.values()),
        "result_bytes": sum(item["result_bytes"] for item in tasks.values()),
        "success_rate": sum(item["success"] for item in tasks.values()) / len(tasks),
        "skill_description_bytes": sum(item["description_bytes"] for item in skills.values()),
        "skill_bytes": sum(item["skill_bytes"] for item in skills.values()),
    }
    return {
        "servers": {"platform": definitions, "platform-with-factory": with_factory},
        "skills": skills,
        "tasks": tasks,
        "totals": totals,
    }


def _datasets(workdir: Path) -> DirectoryDatasetStore:
    """A year of daily returns for a fund, its benchmark, and three assets, as one dataset."""
    columns = {"fund": daily_returns(1), "benchmark": daily_returns(2)}
    columns |= {name: daily_returns(seed) for seed, name in enumerate(ASSETS, 3)}
    days = (date(2025, 1, 1) + timedelta(days=step) for step in range(400))
    business_days = [day for day in days if day.weekday() < 5][:252]
    lines = ["date," + ",".join(columns)]
    lines += [
        f"{business_days[row]}," + ",".join(str(values[row]) for values in columns.values())
        for row in range(252)
    ]
    source = workdir / "daily.csv"
    source.write_text("\n".join(lines) + "\n", "utf-8")
    store = DirectoryDatasetStore(workdir / "datasets")
    store.put("daily", source)
    return store


def run() -> dict[str, Any]:
    """Compose every toolset as the platform server does, run every task, return the metrics."""
    research = open_research(":memory:")
    workdir = tempfile.TemporaryDirectory()
    try:
        research.ingest.ingest(CORPUS)
        store = _datasets(Path(workdir.name))
        policy = PermissionPolicy()
        default = compose(policy=policy, research=research.research, datasets=store)
        everything = compose(
            policy=policy,
            research=research.research,
            datasets=store,
            factory=AppFactory(PytemplateUvScaffolder()),
        )
        return anyio.run(_evaluate, default, everything, workdir.name)
    finally:
        research.close()
        workdir.cleanup()


if __name__ == "__main__":
    metrics = run()
    print(json.dumps(metrics, indent=2))
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"Baseline written to {BASELINE.relative_to(ROOT)}")
