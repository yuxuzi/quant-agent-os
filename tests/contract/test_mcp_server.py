"""The MCP stdio transport forwards to the adapter: same tools, same results, same errors."""

from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from portfolio_tools import build_registry
from pydantic import BaseModel

from quant_agent_os.mcp import build_server, call_tool
from quant_agent_os.runtime import PlanStep, ToolExecutor
from quant_agent_os.tools import Permission, PermissionPolicy, ToolRegistry, tool

ARGUMENTS = {"series_id": "s", "returns": [0.01, -0.02, 0.03], "periods_per_year": 12}


class Query(BaseModel):
    url: str


@tool(name="fetch_page", description="Needs the network.", permissions=[Permission.NETWORK])
def fetch_page(params: Query) -> Query:
    return params


REGISTRY = ToolRegistry([*build_registry().specs(), fetch_page])
EXECUTOR = ToolExecutor(REGISTRY, PermissionPolicy())


def _session(check: Any) -> Any:
    async def main() -> Any:
        server = build_server(EXECUTOR, name="test", instructions="Use the tools.")
        async with create_connected_server_and_client_session(server) as session:
            return await check(session)

    return anyio.run(main)


def test_tools_list_mirrors_the_registry_with_behaviour_hints() -> None:
    async def check(session: ClientSession) -> Any:
        return (await session.list_tools()).tools

    tools = {item.name: item for item in _session(check)}

    assert sorted(tools) == ["annualized_return", "fetch_page", "max_drawdown"]
    assert tools["max_drawdown"].annotations.readOnlyHint is True
    assert tools["max_drawdown"].annotations.openWorldHint is False
    assert tools["fetch_page"].annotations.openWorldHint is True
    assert (
        tools["max_drawdown"].inputSchema
        == REGISTRY.get("max_drawdown").descriptor()["input_schema"]
    )


def test_tool_results_equal_direct_executor_calls() -> None:
    async def check(session: ClientSession) -> Any:
        return await session.call_tool("annualized_return", ARGUMENTS)

    result = _session(check)
    direct = EXECUTOR.execute(PlanStep(tool="annualized_return", arguments=ARGUMENTS))

    assert result.isError is False
    assert result.structuredContent == direct.output
    assert (
        result.content[0].text
        == call_tool(EXECUTOR, "annualized_return", ARGUMENTS)["content"][0]["text"]
    )


def test_invalid_input_and_denied_calls_report_executor_errors() -> None:
    async def check(session: ClientSession) -> Any:
        invalid = await session.call_tool("max_drawdown", {"series_id": "s", "returns": []})
        denied = await session.call_tool("fetch_page", {"url": "https://example.com"})
        unknown = await session.call_tool("place_trade", {})
        return invalid, denied, unknown

    invalid, denied, unknown = _session(check)

    assert invalid.isError and invalid.content[0].text.startswith("invalid_input:")
    assert denied.isError and denied.content[0].text.startswith("denied:")
    assert unknown.isError and unknown.content[0].text.startswith("unknown_tool:")


def test_the_server_announces_its_name_version_and_instructions() -> None:
    options = build_server(
        EXECUTOR, name="test", instructions="Use the tools."
    ).create_initialization_options()
    name, version, instructions = options.server_name, options.server_version, options.instructions

    assert name == "test" and version and instructions == "Use the tools."
