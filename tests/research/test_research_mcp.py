"""End to end over a real stdio subprocess, the way Claude Code runs the server."""

import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from quant_agent_os.runs import SqliteRunStore
from quant_platform.cli import app as cli


def _server(store: Path, *extra: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "quant_platform.cli",
            "serve",
            "--toolsets",
            "research",
            "--store",
            str(store),
            *extra,
        ],
        env=dict(os.environ) | {"QUANT_AGENT_OS_HOME": str(store.parent)},
    )


def _run(parameters: StdioServerParameters, check: Any) -> Any:
    async def main() -> Any:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                return await check(session, initialised)

    return anyio.run(main)


def test_claude_style_client_gets_cited_answers(corpus_dir: Path, tmp_path: Path) -> None:
    store = tmp_path / "kb.db"
    ingested = CliRunner().invoke(cli, ["ingest", str(corpus_dir), "--store", str(store)])
    assert ingested.exit_code == 0, ingested.output

    async def check(session: ClientSession, initialised: Any) -> Any:
        tools = (await session.list_tools()).tools
        found = await session.call_tool(
            "find_related_signals", {"query": "What is related to short-term reversal?"}
        )
        chunk_id = found.structuredContent["passages"][0]["chunk_id"]
        passage = await session.call_tool("get_source_passage", {"chunk_id": chunk_id})
        return initialised, tools, found, passage

    initialised, tools, found, passage = _run(_server(store), check)

    assert initialised.serverInfo.name == "quant-agent-os"
    assert "cite every claim" in (initialised.instructions or "")
    assert sorted(item.name for item in tools) == [
        "find_related_signals",
        "get_source_passage",
        "research_report",
        "server_info",
    ]
    assert all(item.annotations.readOnlyHint for item in tools)
    packet = found.structuredContent
    assert found.isError is False
    assert "signal:order-flow-imbalance" in {entity["id"] for entity in packet["related"]}
    assert all(item["source"]["location"] for item in packet["passages"])
    assert packet["data_versions"]["corpus_snapshot"].startswith("snap:")
    assert passage.structuredContent["evidence"][0]["kind"] == "retrieved"


def test_serving_refuses_a_missing_store(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["serve", "--store", str(tmp_path / "absent.db")])

    assert result.exit_code == 1
    assert "run qaos ingest" in " ".join(result.output.split())


def test_every_mcp_call_is_recorded_as_a_run(corpus_dir: Path, tmp_path: Path) -> None:
    store = tmp_path / "kb.db"
    runner = CliRunner()
    assert runner.invoke(cli, ["ingest", str(corpus_dir), "--store", str(store)]).exit_code == 0

    async def check(session: ClientSession, initialised: Any) -> Any:
        await session.call_tool("find_related_signals", {"query": "short-term reversal"})
        await session.call_tool("get_source_passage", {"chunk_id": "missing"})

    _run(_server(store), check)
    with SqliteRunStore(tmp_path / "runs.db") as runs:
        recorded = runs.list_runs()
        searched = runs.get_run(recorded[-1].run_id)
    listing = runner.invoke(cli, ["runs", "--runs", str(tmp_path / "runs.db")])
    shown = runner.invoke(cli, ["runs", recorded[-1].run_id, "--runs", str(tmp_path / "runs.db")])

    assert [(run.goal, run.status, run.actor) for run in recorded] == [
        ("get_source_passage", "failed", "mcp"),
        ("find_related_signals", "completed", "mcp"),
    ]
    assert searched is not None
    assert searched.run.data_versions["corpus_snapshot"].startswith("snap:")
    assert searched.calls[0].input_sha256
    assert listing.exit_code == 0 and "find_related_signals" in listing.output
    assert shown.exit_code == 0 and "tool.called" in shown.output


def test_serving_without_recording_writes_no_run_store(corpus_dir: Path, tmp_path: Path) -> None:
    store = tmp_path / "kb.db"
    assert (
        CliRunner().invoke(cli, ["ingest", str(corpus_dir), "--store", str(store)]).exit_code == 0
    )

    async def check(session: ClientSession, initialised: Any) -> Any:
        await session.call_tool("find_related_signals", {"query": "momentum"})

    _run(_server(store, "--no-record"), check)

    assert not (tmp_path / "runs.db").exists()
    assert (
        "No runs recorded"
        in CliRunner().invoke(cli, ["runs", "--runs", str(tmp_path / "runs.db")]).output
    )


def test_ingest_takes_a_firm_vocabulary_and_refuses_a_bad_one(
    corpus_dir: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    store = tmp_path / "kb.db"
    bad = tmp_path / "bad.toml"
    bad.write_text('version = "x"\nentities = []\n', "utf-8")

    refused = runner.invoke(
        cli, ["ingest", str(corpus_dir), "--store", str(store), "--vocabulary", str(bad)]
    )
    assert refused.exit_code == 1
    assert "bad.toml" in refused.stderr
    assert not store.exists()

    firm = tmp_path / "firm.toml"
    firm.write_text(
        'version = "firm-1"\n[[entities]]\nid = "signal:desk"\ntype = "signal"\nname = "Desk"\n',
        "utf-8",
    )
    done = runner.invoke(
        cli, ["ingest", str(corpus_dir), "--store", str(store), "--vocabulary", str(firm)]
    )
    assert done.exit_code == 0, done.output
    assert "quant-v1+firm-1." in done.output
