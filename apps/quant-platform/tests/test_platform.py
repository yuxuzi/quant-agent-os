"""The platform server (plan steps P2.1, P2.4, P2.5): one catalog, research_report, and prompts."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from typer.testing import CliRunner

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.mcp import build_server
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_app_factory import AppFactory, PytemplateUvScaffolder
from quant_platform import Catalog, compose
from quant_platform.cli import app as cli
from quant_research import ResearchApp, open_research

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "packages" / "quant-research" / "tests" / "fixtures" / "corpus"
FUND = [0.012, -0.004, 0.021, -0.015, 0.008, 0.017, -0.006, 0.011, 0.002, -0.009, 0.014, 0.005]


@pytest.fixture(scope="module")
def research() -> Iterator[ResearchApp]:
    app = open_research(":memory:")
    app.ingest.ingest(CORPUS)
    yield app
    app.close()


@pytest.fixture
def datasets(tmp_path: Path) -> DirectoryDatasetStore:
    source = tmp_path / "returns.csv"
    source.write_text(
        "month,fund\n" + "".join(f"2024-{m + 1:02d},{v}\n" for m, v in enumerate(FUND))
    )
    store = DirectoryDatasetStore(tmp_path / "datasets")
    store.put("returns", source)
    return store


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
        "generate_strategy",
        "analyze_performance",
        "analyze_risk",
        "list_datasets",
        "research_report",
        "describe_template",
        "check_spec",
        "plan_app",
    ]
    assert [r.uri_template for r in catalog.resources] == [
        "qaos://passage/{chunk_id}",
        "qaos://strategy/{focus}",
        "qaos://dataset/{dataset_id}",
        "qaos://template/{template}",
        "qaos://template/{template}/{profile}",
    ]
    assert [p.name for p in catalog.prompts] == [
        "research_signal",
        "analyze_returns",
        "report_on_signal",
        "draft_app_spec",
    ]
    assert "list_datasets" in catalog.instructions and "check_spec" in catalog.instructions


def test_a_toolset_alone_brings_only_its_own_parts(datasets: DirectoryDatasetStore) -> None:
    catalog = compose(policy=PermissionPolicy(), datasets=datasets)

    assert [spec.name for spec in catalog.tools] == [
        "analyze_performance",
        "analyze_risk",
        "list_datasets",
    ]
    assert [p.name for p in catalog.prompts] == ["analyze_returns"]


def test_research_report_is_cited_deterministic_and_honours_the_contract(
    research: ResearchApp, datasets: DirectoryDatasetStore
) -> None:
    report = next(s for s in _everything(research, datasets).tools if s.name == "research_report")

    record = check_tool(report, {"focus": "momentum"})
    assert record.output is not None
    assert [step["tool"] for step in record.output["steps"]] == ["generate_strategy"]
    assert record.output["evidence"] and record.output["open_questions"]
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


def test_research_report_without_analytics_refuses_a_series(research: ResearchApp) -> None:
    catalog = compose(policy=PermissionPolicy(), research=research.research)
    executor = ToolExecutor(ToolRegistry(catalog.tools), PermissionPolicy())

    record = executor.execute(
        PlanStep(
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
            "analyze_returns", {"dataset": "returns", "column": "fund", "periods_per_year": "12"}
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
    no_store = runner.invoke(cli, ["serve-mcp", "--toolsets", "research"])
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
        lambda name, *args: None if name == "quant_app_factory" else real(name, *args),
    )

    asked = CliRunner().invoke(cli, ["catalog", "--toolsets", "factory"])

    assert composition.available_toolsets() == ["research", "analytics"]
    assert asked.exit_code == 2 and "quant-agent-os-platform[factory]" in asked.output
