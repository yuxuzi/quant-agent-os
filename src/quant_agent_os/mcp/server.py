"""MCP server transport over stdio (optional extra ``quant-agent-os[mcp]``).

The server only forwards: ``tools/list`` to `tool_definitions` and ``tools/call`` to `call_tool`,
so MCP clients get exactly the executor's validation, permissions, limits, and records
(decision 0002). Input is validated by the executor's pydantic models, not by the SDK, so an
invalid call over MCP fails the same way as a direct call. Tool calls run on a worker thread
to keep the event loop responsive.

stdout carries the protocol, so nothing else may write to it while the server runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quant_agent_os import __version__
from quant_agent_os.mcp.adapters import call_tool, tool_definitions
from quant_agent_os.runtime.executor import ToolExecutor

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

INSTALL_HINT = "install the MCP extra: uv add 'quant-agent-os[mcp]'"


class MCPUnavailableError(RuntimeError):
    """Raised when the MCP SDK (the ``mcp`` extra) is not installed."""


def build_server(
    executor: ToolExecutor, *, name: str, instructions: str | None = None
) -> Server[Any, Any]:
    """Return an MCP server whose tools are the executor's allowed tools."""
    try:
        import anyio
        from mcp import types
        from mcp.server.lowlevel import Server
    except ImportError as error:
        raise MCPUnavailableError(f"MCP support is not installed; {INSTALL_HINT}.") from error

    server: Server[Any, Any] = Server(name, version=__version__, instructions=instructions)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition["name"],
                description=definition["description"],
                inputSchema=definition["inputSchema"],
                annotations=types.ToolAnnotations(**definition["annotations"]),
            )
            for definition in tool_definitions(executor)
        ]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def handle_call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        result = await anyio.to_thread.run_sync(call_tool, executor, name, arguments)
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=item["text"]) for item in result["content"]
            ],
            structuredContent=result.get("structuredContent"),
            isError=result["isError"],
        )

    return server


async def serve_stdio(
    executor: ToolExecutor, *, name: str, instructions: str | None = None
) -> None:
    """Serve the executor's tools over stdin and stdout until the client disconnects."""
    server = build_server(executor, name=name, instructions=instructions)
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(executor: ToolExecutor, *, name: str, instructions: str | None = None) -> None:
    """Blocking entry point for console commands; raises `MCPUnavailableError` without the extra."""
    try:
        import anyio
    except ImportError as error:
        raise MCPUnavailableError(f"MCP support is not installed; {INSTALL_HINT}.") from error

    anyio.run(lambda: serve_stdio(executor, name=name, instructions=instructions))
