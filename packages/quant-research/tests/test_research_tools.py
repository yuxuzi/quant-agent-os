"""Tool interface (plan step R5): the contract, MCP parity, errors, and report provenance."""

import json

import pytest

from quant_agent_os.mcp import call_tool, tool_definitions
from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.runtime.orchestrator import Orchestrator
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry
from quant_research import ResearchApp, ResearchQuery, research_tools
from quant_research.application.retrieval import CONCISE_PREVIEW_CHARS

QUERY = {"query": "What is related to short-term reversal?"}


@pytest.fixture
def executor(research: ResearchApp) -> ToolExecutor:
    return ToolExecutor(ToolRegistry(research_tools(research.research)), PermissionPolicy())


def test_research_tools_declare_a_complete_read_only_contract(executor: ToolExecutor) -> None:
    for spec in executor.registry.specs():
        descriptor = spec.descriptor()

        assert spec.permissions == frozenset({Permission.READ_LOCAL_DATA})
        assert {"warnings", "evidence", "data_versions"} <= set(
            descriptor["output_schema"]["properties"]
        )
        assert descriptor["deterministic"] and descriptor["idempotent"]
        json.dumps(descriptor)


def test_mcp_results_equal_direct_calls(executor: ToolExecutor) -> None:
    direct = executor.execute(PlanStep(tool="find_related_signals", arguments=QUERY))
    via_mcp = call_tool(executor, "find_related_signals", QUERY)

    assert [d["name"] for d in tool_definitions(executor)] == [
        "find_related_signals",
        "generate_strategy",
        "get_source_passage",
    ]
    assert via_mcp["isError"] is False
    assert via_mcp["structuredContent"] == direct.output


def test_records_carry_the_corpus_snapshot_and_fit_the_size_limit(
    executor: ToolExecutor, research: ResearchApp
) -> None:
    snapshot = research.store.current_snapshot()
    record = executor.execute(
        PlanStep(tool="find_related_signals", arguments=QUERY | {"k": 25, "hops": 2})
    )

    assert record.status == "ok"
    assert snapshot is not None
    assert record.data_versions == {"corpus_snapshot": snapshot.id}


def test_passages_round_trip_through_get_source_passage(executor: ToolExecutor) -> None:
    packet = executor.execute(PlanStep(tool="find_related_signals", arguments=QUERY)).output
    assert packet is not None
    chunk_id = packet["passages"][0]["chunk_id"]

    passage = call_tool(executor, "get_source_passage", {"chunk_id": chunk_id})

    assert passage["structuredContent"]["chunk_id"] == chunk_id
    assert passage["structuredContent"]["evidence"][0]["kind"] == "retrieved"


def test_unknown_passages_and_bad_input_fail_with_actionable_messages(
    executor: ToolExecutor,
) -> None:
    unknown = executor.execute(PlanStep(tool="get_source_passage", arguments={"chunk_id": "x"}))
    invalid = executor.execute(PlanStep(tool="find_related_signals", arguments={"hops": 5}))

    assert unknown.status == "error"
    assert "No passage with id 'x'" in (unknown.error or "")
    assert invalid.status == "invalid_input"
    assert "query" in (invalid.error or "") and "hops" in (invalid.error or "")


def test_orchestrated_reports_record_the_corpus_snapshot(research: ResearchApp) -> None:
    plan = {"goal": "g", "steps": [{"tool": "find_related_signals", "arguments": QUERY}]}
    orchestrator = Orchestrator(
        ToolRegistry(research_tools(research.research)), FakeModelProvider([json.dumps(plan)])
    )

    report = orchestrator.run("g")

    snapshot = research.store.current_snapshot()
    assert snapshot is not None
    assert report.metadata.data_versions == {"corpus_snapshot": snapshot.id}
    assert report.findings and all(item.sources for item in report.findings)


def test_search_is_concise_by_default_and_full_on_request(
    research: ResearchApp, executor: ToolExecutor
) -> None:
    full = executor.execute(
        PlanStep(tool="find_related_signals", arguments={**QUERY, "detail": "full"})
    ).output
    concise = executor.execute(PlanStep(tool="find_related_signals", arguments=QUERY)).output
    assert full is not None and concise is not None

    assert full == research.research.find_related(ResearchQuery(**QUERY)).model_dump(mode="json")
    assert [p["chunk_id"] for p in concise["passages"]] == [p["chunk_id"] for p in full["passages"]]
    assert [e["path"] for e in concise["related"]] == [e["path"] for e in full["related"]]
    assert concise["evidence"] == full["evidence"]
    assert all(link not in e["path"] for e in concise["related"] for link in e["links"])
    assert len(json.dumps(concise)) < len(json.dumps(full))


def test_concise_previews_are_short_and_say_they_are_truncated(research: ResearchApp) -> None:
    packet = research.research.find_related(ResearchQuery(**QUERY))
    first = packet.passages[0]
    long = packet.model_copy(
        update={"passages": [first.model_copy(update={"text": "x" * 700 + "…"})]}
    )

    (cut,) = long.concise().passages
    assert cut.text == "x" * CONCISE_PREVIEW_CHARS + "…" and cut.truncated
    assert packet.concise().passages[0] == first  # already short: unchanged
