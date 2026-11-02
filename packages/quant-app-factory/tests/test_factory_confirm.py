"""create_app over MCP, confirmed in the client (B-35, decision 0014): opt-in, local stdio only."""

import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from quant_agent_os.runs import SqliteRunStore


def _serve(tmp_path: Path, *flags: str, answer: types.ElicitResult) -> Any:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "quant_app_factory.cli",
            "serve-mcp",
            "--runs",
            str(tmp_path / "runs.db"),
            *flags,
        ],
        env=dict(os.environ) | {"HOME": str(tmp_path)},
    )
    asked: list[str] = []

    async def respond(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        asked.append(params.message)
        return answer

    async def main() -> Any:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write, elicitation_callback=respond) as session,
            ):
                await session.initialize()
                tools = [tool.name for tool in (await session.list_tools()).tools]
                if "create_app" not in tools:
                    return tools, None
                spec = {
                    "name": "momo-lab",
                    "kind": "quant-research",
                    "description": "Momentum research",
                    "output_dir": str(tmp_path / "apps"),
                }
                created = await session.call_tool("create_app", {"spec": spec})
                return tools, created

    tools, created = anyio.run(main)
    return tools, created, asked


def test_create_app_is_served_only_with_the_opt_in(tmp_path: Path) -> None:
    tools, created, asked = _serve(tmp_path, answer=types.ElicitResult(action="decline"))

    assert "create_app" not in tools and "check_app" not in tools
    assert created is None and asked == []


def test_a_declined_confirmation_creates_nothing(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    tools, created, asked = _serve(
        tmp_path, "--confirm-in-client", answer=types.ElicitResult(action="decline")
    )

    assert {"create_app", "check_app"} <= set(tools)
    assert asked and "create_app" in asked[0] and "write_local_data" in asked[0]
    assert created.isError and "approval_required" in created.content[0].text
    assert list((tmp_path / "apps").iterdir()) == []
    store = SqliteRunStore(tmp_path / "runs.db")
    try:
        (run,) = store.list_runs(5)
        assert run.status == "awaiting_approval"
    finally:
        store.close()
