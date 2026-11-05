"""End to end over a real stdio subprocess, the way Claude Code runs the platform server."""

import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from quant_agent_os.runs import SqliteRunStore
from quant_research.cli import app as research_cli

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "research" / "fixtures" / "corpus"


def test_one_server_offers_the_default_toolsets_and_records_each_call(tmp_path: Path) -> None:
    store, runs, datasets = tmp_path / "kb.db", tmp_path / "runs.db", tmp_path / "datasets"
    ingested = CliRunner().invoke(research_cli, ["ingest", str(CORPUS), "--store", str(store)])
    assert ingested.exit_code == 0, ingested.output
    datasets.mkdir()
    (datasets / "monthly.csv").write_text(
        "month,fund\n" + "".join(f"2024-{m:02d},{0.01 * (-1) ** m + 0.002}\n" for m in range(1, 13))
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "quant_platform.cli",
            "serve-mcp",
            "--store",
            str(store),
            "--datasets",
            str(datasets),
            "--runs",
            str(runs),
        ],
        env=dict(os.environ),
    )

    async def main() -> Any:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                tools = (await session.list_tools()).tools
                templates = (await session.list_resource_templates()).resourceTemplates
                prompts = (await session.list_prompts()).prompts
                report = await session.call_tool(
                    "research_report",
                    {"focus": "momentum", "returns": {"dataset": "monthly", "column": "fund"}},
                )
                dataset = await session.read_resource("qaos://dataset/monthly")  # type: ignore[arg-type]
                fixed = (await session.list_resources()).resources
                info = await session.read_resource("qaos://server/info")  # type: ignore[arg-type]
                return initialised, tools, templates, prompts, report, dataset, fixed, info

    initialised, tools, templates, prompts, report, dataset, fixed, info = anyio.run(main)

    assert initialised.serverInfo.name == "quant-agent-os"
    assert initialised.serverInfo.version == version("quant-agent-os")
    assert [str(resource.uri) for resource in fixed] == ["qaos://server/info"]
    assert json.loads(info.contents[0].text)["platform_version"] == initialised.serverInfo.version
    assert {"research_report", "analyze_performance"} <= {t.name for t in tools}
    assert "check_spec" not in {t.name for t in tools}  # App Factory is asked for by name
    assert "qaos://passage/{chunk_id}" in {t.uriTemplate for t in templates}
    assert "report_on_signal" in {p.name for p in prompts}
    assert report.isError is False
    assert report.structuredContent["metrics"]["sharpe_ratio"] is not None
    assert json.loads(dataset.contents[0].text)["datasets"][0]["rows"] == 12
    run_store = SqliteRunStore(runs)
    try:
        recorded = [run.actor for run in run_store.list_runs(10)]
    finally:
        run_store.close()
    assert recorded == ["mcp", "mcp", "mcp"]  # the report and the two resource reads
