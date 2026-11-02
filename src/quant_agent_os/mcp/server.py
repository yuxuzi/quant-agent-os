"""MCP server transport over stdio (optional extra ``quant-agent-os[mcp]``).

The server only forwards: ``tools/list`` to `tool_definitions` and ``tools/call`` to `call_tool`,
so MCP clients get exactly the executor's validation, permissions, limits, and records
(decision 0002). Input is validated by the executor's pydantic models, not by the SDK, so an
invalid call over MCP fails the same way as a direct call. Tool calls run on a worker thread
to keep the event loop responsive.

stdout carries the protocol, so nothing else may write to it while the server runs.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from quant_agent_os import __version__
from quant_agent_os.mcp.adapters import (
    ResourceReadError,
    call_tool,
    get_prompt,
    prompt_definitions,
    read_resource,
    resource_templates,
    served_resources,
    tool_definitions,
)
from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.tools.catalog import PromptArgumentError, PromptSpec, ResourceSpec

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

INSTALL_HINT = "install the MCP extra: uv add 'quant-agent-os[mcp]'"


class MCPUnavailableError(RuntimeError):
    """Raised when the MCP SDK (the ``mcp`` extra) is not installed."""


def build_server(
    executor: ToolExecutor,
    *,
    name: str,
    instructions: str | None = None,
    recorder: RunRecorder | None = None,
    resources: list[ResourceSpec] | None = None,
    prompts: list[PromptSpec] | None = None,
) -> Server[Any, Any]:
    """Return an MCP server whose tools are the executor's allowed tools.

    ``resources`` are served when their tool is; reading one calls that tool. ``prompts`` are
    offered as they are. A server without either does not advertise them. With a ``recorder``,
    every tool call and resource read is persisted as a one-call run.
    """
    try:
        import anyio
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        from mcp.shared.exceptions import McpError
    except ImportError as error:
        raise MCPUnavailableError(f"MCP support is not installed; {INSTALL_HINT}.") from error
    resources = served_resources(executor, resources or [])
    prompts = prompts or []

    server: Server[Any, Any] = Server(name, version=__version__, instructions=instructions)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition["name"],
                title=definition["title"],
                description=definition["description"],
                inputSchema=definition["inputSchema"],
                outputSchema=definition["outputSchema"],
                annotations=types.ToolAnnotations(**definition["annotations"]),
            )
            for definition in tool_definitions(executor)
        ]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def handle_call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        result = await anyio.to_thread.run_sync(
            functools.partial(call_tool, executor, name, arguments, recorder=recorder)
        )
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=item["text"]) for item in result["content"]
            ],
            structuredContent=result.get("structuredContent"),
            isError=result["isError"],
        )

    if resources:

        @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_resources() -> list[types.Resource]:
            return []  # every resource is a template; registering this advertises them

        @server.list_resource_templates()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_templates() -> list[types.ResourceTemplate]:
            return [
                types.ResourceTemplate(**definition)
                for definition in resource_templates(executor, resources)
            ]

        @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_read(uri: Any) -> list[ReadResourceContents]:
            try:
                contents = await anyio.to_thread.run_sync(
                    functools.partial(
                        read_resource, executor, resources, str(uri), recorder=recorder
                    )
                )
            except ResourceReadError as error:
                raise McpError(
                    types.ErrorData(code=types.INVALID_PARAMS, message=str(error))
                ) from None
            return [ReadResourceContents(content=contents["text"], mime_type=contents["mimeType"])]

    if prompts:

        @server.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_prompts() -> list[types.Prompt]:
            return [types.Prompt.model_validate(item) for item in prompt_definitions(prompts)]

        @server.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_prompt(
            prompt: str, arguments: dict[str, str] | None
        ) -> types.GetPromptResult:
            try:
                return types.GetPromptResult.model_validate(get_prompt(prompts, prompt, arguments))
            except PromptArgumentError as error:
                raise McpError(
                    types.ErrorData(code=types.INVALID_PARAMS, message=str(error))
                ) from None

    return server


async def serve_stdio(
    executor: ToolExecutor,
    *,
    name: str,
    instructions: str | None = None,
    recorder: RunRecorder | None = None,
    resources: list[ResourceSpec] | None = None,
    prompts: list[PromptSpec] | None = None,
) -> None:
    """Serve the executor's tools (and any resources and prompts) over stdin and stdout."""
    server = build_server(
        executor,
        name=name,
        instructions=instructions,
        recorder=recorder,
        resources=resources,
        prompts=prompts,
    )
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio(
    executor: ToolExecutor,
    *,
    name: str,
    instructions: str | None = None,
    recorder: RunRecorder | None = None,
    resources: list[ResourceSpec] | None = None,
    prompts: list[PromptSpec] | None = None,
) -> None:
    """Blocking entry point for console commands; raises `MCPUnavailableError` without the extra."""
    try:
        import anyio
    except ImportError as error:
        raise MCPUnavailableError(f"MCP support is not installed; {INSTALL_HINT}.") from error

    anyio.run(
        lambda: serve_stdio(
            executor,
            name=name,
            instructions=instructions,
            recorder=recorder,
            resources=resources,
            prompts=prompts,
        )
    )
