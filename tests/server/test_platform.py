"""The platform server (plan steps P2.1, P2.4, P2.5): one catalog, research_report, and prompts."""

import json
import subprocess
import sys
from importlib.metadata import version
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from typer.testing import CliRunner

import quant_platform.cli as platform_cli
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import build_server
from quant_agent_os.runtime import ToolCall, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_app_factory import AppFactory, PytemplateUvScaffolder
from quant_platform import Catalog, compose
from quant_platform.cli import app as cli
from quant_platform.cli_common import parse_toolsets
from quant_research import ResearchApp

FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]


def _everything(research: ResearchApp, datasets: DirectoryDatasetStore) -> Catalog:
    return compose(
        policy=PermissionPolicy(),
        research=research.research,
        datasets=datasets,
        factory=AppFactory(PytemplateUvScaffolder()),
    )


def test_one_catalog_serves_every_toolset(
    research: ResearchApp, datasets: DirectoryDatasetStore
) -> None:
    catalog = _everything(research, datasets)

    assert [spec.name for spec in catalog.tools] == [
        "find_related_signals",
        "get_source_passage",
        "analyze_performance",
        "analyze_risk",
        "list_datasets",
        "research_report",
        "describe_template",
        "check_spec",
        "server_info",
    ]
    assert [r.uri_template for r in catalog.resources] == [
        "qaos://passage/{chunk_id}",
        "qaos://dataset/{dataset_id}",
        "qaos://template/{template}",
        "qaos://template/{template}/{profile}",
        "qaos://server/info",
    ]
    assert [p.name for p in catalog.prompts] == [
        "research_signal",
        "analyze_returns",
        "report_on_signal",
        "draft_app_spec",
    ]
    assert "list_datasets" in catalog.instructions and "check_spec" in catalog.instructions
    assert catalog.toolsets == {
        "find_related_signals": "research",
        "get_source_passage": "research",
        "analyze_performance": "analytics",
        "analyze_risk": "analytics",
        "list_datasets": "analytics",
        "research_report": "research",
        "describe_template": "factory",
        "check_spec": "factory",
        "server_info": "platform",
    }


def test_a_toolset_alone_brings_only_its_own_parts(datasets: DirectoryDatasetStore) -> None:
    catalog = compose(policy=PermissionPolicy(), datasets=datasets)

    assert [spec.name for spec in catalog.tools] == [
        "analyze_performance",
        "analyze_risk",
        "list_datasets",
        "server_info",
    ]
    assert [p.name for p in catalog.prompts] == ["analyze_returns"]


def test_server_info_reports_the_platform_and_every_tool(datasets: DirectoryDatasetStore) -> None:
    catalog = compose(policy=PermissionPolicy(), datasets=datasets)
    registry = ToolRegistry(catalog.tools)
    executor = ToolExecutor(registry, PermissionPolicy())

    record = executor.execute(ToolCall(tool="server_info", arguments={}))

    assert record.status == "ok" and record.output is not None
    assert record.output["platform_version"] == version("quant-agent-os")
    assert record.output["registry_digest"] == registry.digest()
    assert [(t["name"], t["version"]) for t in record.output["tools"]] == [
        (spec.name, spec.version) for spec in registry.specs()
    ]


def test_research_report_is_cited_deterministic_and_honours_the_contract(
    research: ResearchApp, datasets: DirectoryDatasetStore
) -> None:
    report = next(s for s in _everything(research, datasets).tools if s.name == "research_report")

    record = check_tool(report, {"focus": "momentum"})
    assert record.output is not None
    assert [step["tool"] for step in record.output["steps"]] == ["generate_strategy"]
    assert record.output["evidence"] and record.output["spec"]["open_questions"]
    assert all(e["kind"] == "retrieved" for e in record.output["evidence"])
    assert "corpus_snapshot" in record.data_versions


def test_research_report_measures_a_named_series(
    research: ResearchApp, datasets: DirectoryDatasetStore
) -> None:
    report = next(s for s in _everything(research, datasets).tools if s.name == "research_report")

    record = check_tool(
        report, {"focus": "momentum", "returns": {"dataset": "returns", "column": "fund"}}
    )
    assert record.output is not None
    assert [step["status"] for step in record.output["steps"]] == ["ok", "ok"]
    assert record.output["metrics"]["sharpe_ratio"] is not None
    assert {e["kind"] for e in record.output["evidence"]} == {"retrieved", "computed"}
    assert set(record.data_versions) == {"corpus_snapshot", "dataset:returns"}
    assert any("Annualized return" in e["statement"] for e in record.output["evidence"]), (
        "metrics come from the dates, with no frequency given"
    )


def test_research_report_without_analytics_refuses_a_series(research: ResearchApp) -> None:
    catalog = compose(policy=PermissionPolicy(), research=research.research)
    executor = ToolExecutor(ToolRegistry(catalog.tools), PermissionPolicy())

    record = executor.execute(
        ToolCall(
            tool="research_report",
            arguments={"focus": "momentum", "returns": {"dataset": "r", "column": "c"}},
        )
    )

    assert record.status == "error" and "no analytics toolset" in (record.error or "")


def test_a_client_reads_resources_and_prompts_from_the_composed_server(
    research: ResearchApp, datasets: DirectoryDatasetStore
) -> None:
    catalog = _everything(research, datasets)
    executor = ToolExecutor(ToolRegistry(catalog.tools), PermissionPolicy())
    server = build_server(
        executor,
        name="quant-agent-os",
        instructions=catalog.instructions,
        resources=catalog.resources,
        prompts=catalog.prompts,
    )

    async def check(session: ClientSession) -> Any:
        found = await session.call_tool("find_related_signals", {"query": "momentum"})
        chunk_id = found.structuredContent["passages"][0]["chunk_id"]  # type: ignore[index]
        passage = await session.read_resource(f"qaos://passage/{chunk_id}")  # type: ignore[arg-type]
        dataset = await session.read_resource("qaos://dataset/returns")  # type: ignore[arg-type]
        template = await session.read_resource("qaos://template/quant-baseline")  # type: ignore[arg-type]
        prompt = await session.get_prompt(
            "analyze_returns", {"dataset": "returns", "column": "fund"}
        )
        return chunk_id, passage, dataset, template, prompt

    async def main() -> Any:
        async with create_connected_server_and_client_session(server) as session:
            return await check(session)

    chunk_id, passage, dataset, template, prompt = anyio.run(main)

    assert json.loads(passage.contents[0].text)["chunk_id"] == chunk_id
    assert json.loads(dataset.contents[0].text)["datasets"][0]["rows"] == len(FUND)
    assert json.loads(template.contents[0].text)["description"]
    assert '{"dataset": "returns", "column": "fund"}' in prompt.messages[0].content.text


def test_the_cli_lists_the_catalog_and_refuses_bad_choices(
    datasets: DirectoryDatasetStore,
) -> None:
    runner = CliRunner()
    listed = runner.invoke(
        cli, ["catalog", "--toolsets", "analytics", "--datasets", str(datasets.root)]
    )
    no_store = runner.invoke(cli, ["serve", "--toolsets", "research"])
    unknown = runner.invoke(cli, ["catalog", "--toolsets", "trading"])

    assert listed.exit_code == 0 and "list_datasets" in listed.output
    assert "qaos://dataset/{dataset_id}" in listed.output
    assert no_store.exit_code == 2
    assert unknown.exit_code == 2


def test_without_the_factory_extra_the_factory_toolset_is_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    from quant_platform import composition

    real = importlib.util.find_spec
    monkeypatch.setattr(
        composition.importlib.util,
        "find_spec",
        lambda name, *args: None if name == "pytemplate" else real(name, *args),
    )

    asked = CliRunner().invoke(cli, ["catalog", "--toolsets", "factory"])

    assert composition.available_toolsets() == ["research", "analytics"]
    assert asked.exit_code == 2 and "quant-agent-os[factory]" in asked.output


def test_app_factory_is_served_only_when_asked_for() -> None:
    assert parse_toolsets(None) == ["research", "analytics"]
    assert parse_toolsets("research,factory") == ["research", "factory"]


def test_the_default_commands_never_load_sign_in_or_approvals() -> None:
    """OAuth and the approval page stay off the default path (decision 0023)."""
    code = (
        "import sys, quant_platform.cli\n"
        "names = [n for n in sys.modules if n.startswith('quant_platform.identity')]\n"
        "print(sorted(names))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert done.stdout.strip() == "[]"


def test_there_is_no_hosted_command() -> None:
    shown = CliRunner(env={"COLUMNS": "200"}).invoke(platform_cli.app, ["--help"])

    assert shown.exit_code == 0
    assert "hosted" not in shown.output


def test_the_default_server_takes_no_hosted_options() -> None:
    shown = CliRunner(env={"COLUMNS": "200"}).invoke(
        platform_cli.app, ["serve", "--http", "--help"]
    )

    assert shown.exit_code == 0
    for option in ("--tenants", "--approval-url", "--resource-url"):
        assert option not in shown.output
