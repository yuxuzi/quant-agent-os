"""End to end over a real stdio subprocess, the way Claude Code runs the server."""

import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from quant_research.cli import app as cli


def _server(store: Path, *extra: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "quant_research.cli", "serve-mcp", "--store", str(store), *extra],
        env=dict(os.environ),
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

    assert initialised.serverInfo.name == "quant-research"
    assert "cite every claim" in (initialised.instructions or "")
    assert [item.name for item in tools] == ["find_related_signals", "get_source_passage"]
    assert all(item.annotations.readOnlyHint for item in tools)
    packet = found.structuredContent
    assert found.isError is False
    assert "signal:order-flow-imbalance" in {entity["id"] for entity in packet["related"]}
    assert all(item["source"]["location"] for item in packet["passages"])
    assert packet["data_versions"]["corpus_snapshot"].startswith("snap:")
    assert passage.structuredContent["evidence"][0]["kind"] == "retrieved"


def test_serve_mcp_refuses_a_missing_store(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["serve-mcp", "--store", str(tmp_path / "absent.db")])

    assert result.exit_code == 1
    assert "run quant-research ingest" in " ".join(result.output.split())
