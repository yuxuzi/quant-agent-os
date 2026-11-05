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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from quant_agent_os import __version__
from quant_agent_os.mcp.adapters import (
    ResourceReadError,
    call_result,
    call_tool,
    fixed_resources,
    get_prompt,
    prompt_definitions,
    read_resource,
    resource_templates,
    served_resources,
    tool_definitions,
)
from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolCallRecord, ToolExecutor
from quant_agent_os.serving import Surface
from quant_agent_os.tools.catalog import PromptArgumentError, PromptSpec, ResourceSpec

if TYPE_CHECKING:
    from mcp.server.lowlevel import Server

INSTALL_HINT = "install the MCP extra: uv add 'quant-agent-os[mcp]'"


class MCPUnavailableError(RuntimeError):
    """Raised when the MCP SDK (the ``mcp`` extra) is not installed."""


class AccessDeniedError(PermissionError):
    """Raised by a `Route` hook when the caller may not make a request."""


# Runs a call for one caller, opening an approval request if it waits for one (decision
# 0014); returns the record and, for a new request, its id and the approval page's address.
ApprovalGate = Callable[
    [ToolExecutor, ToolCall, RunRecorder], tuple[ToolCallRecord, tuple[str, str] | None]
]


@dataclass(frozen=True)
class Served:
    """What one request is served: its caller's surface, the recorder for that caller, and
    (over HTTP, with an approval page) the gate that turns a waiting call into a request."""

    surface: Surface
    recorder: RunRecorder | None = None
    approvals: ApprovalGate | None = None


# Called with the transport's request and the scope a request needs ("tools:list" to list,
# "tools:call" to call a tool or read a resource); returns what the caller is served (its
# tenant's surface) or raises `AccessDeniedError`. stdio has no hook: the local user is trusted.
Route = Callable[[Any, str], Served]
FORBIDDEN = -32003  # JSON-RPC server error: authenticated, but not allowed this request


def build_server(
    executor: ToolExecutor | None,
    *,
    name: str,
    instructions: str | None = None,
    recorder: RunRecorder | None = None,
    resources: list[ResourceSpec] | None = None,
    prompts: list[PromptSpec] | None = None,
    route: Route | None = None,
    confirm_in_client: bool = False,
) -> Server[Any, Any]:
    """Return an MCP server whose tools are the executor's allowed tools.

    ``resources`` are served when their tool is; reading one calls that tool. ``prompts`` are
    offered as they are. A server without either does not advertise them. With a ``recorder``,
    every tool call and resource read is persisted as a one-call run. Over HTTP, ``route``
    replaces ``executor``, ``recorder``, ``resources``, and ``prompts``: it checks each
    request's scope and returns its caller's tenant surface and recorder (decision 0013).

    With ``confirm_in_client`` (stdio only, and only with a ``recorder``), a call that waits for
    an approval asks the client to confirm it (form elicitation); a yes is recorded as approved
    by ``client-confirmation`` and the call runs in its run (decision 0014).
    """
    if confirm_in_client and (route is not None or recorder is None):
        raise ValueError("confirm_in_client is for stdio with a recorder (a run store).")
    try:
        import anyio
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.server.lowlevel.helper_types import ReadResourceContents
        from mcp.shared.exceptions import McpError, UrlElicitationRequiredError
    except ImportError as error:
        raise MCPUnavailableError(f"MCP support is not installed; {INSTALL_HINT}.") from error
    static: Served | None = None
    if route is None:
        if executor is None:
            raise ValueError("Give an executor, or a route for each request.")
        surface = Surface(executor, served_resources(executor, resources or []), prompts or [])
        static = Served(surface, recorder)

    server: Server[Any, Any] = Server(name, version=__version__, instructions=instructions)

    def serve(scope: str) -> Served:
        """Return what this request is served, after checking it against ``scope``."""
        if static is not None:
            return static
        assert route is not None
        try:
            served = route(server.request_context.request, scope)
        except AccessDeniedError as error:
            raise McpError(types.ErrorData(code=FORBIDDEN, message=str(error))) from None
        surface = served.surface
        return Served(
            Surface(
                surface.executor,
                served_resources(surface.executor, surface.resources),
                surface.prompts,
                surface.run_store,
            ),
            served.recorder,
            served.approvals,
        )

    async def confirmed(
        executor: ToolExecutor, step: ToolCall, record: ToolCallRecord
    ) -> ToolCallRecord:
        """Ask the client to confirm a waiting call; run it in its run if confirmed."""
        assert recorder is not None and record.run_id is not None
        needs = ", ".join(
            sorted(
                permission.value for permission in executor.registry.get(record.tool).permissions
            )
        )
        answer = await server.request_context.session.elicit_form(
            message=(
                f"Run {record.tool} once? It needs {needs}. "
                f"Input SHA-256 {(record.input_sha256 or '')[:16]}."
            ),
            requestedSchema={
                "type": "object",
                "properties": {"approve": {"type": "boolean", "title": "Run it once"}},
                "required": ["approve"],
            },
        )
        if answer.action != "accept" or not (answer.content or {}).get("approve"):
            return record
        run_id = record.run_id

        def approve_and_resume() -> ToolCallRecord:
            recorder.approve(run_id, approved_by="client-confirmation")
            return recorder.resume(executor, run_id, [step])[-1]

        return await anyio.to_thread.run_sync(approve_and_resume)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        served = serve("tools:list")
        return [
            types.Tool(
                name=definition["name"],
                title=definition["title"],
                description=definition["description"],
                inputSchema=definition["inputSchema"],
                outputSchema=definition["outputSchema"],
                annotations=types.ToolAnnotations(**definition["annotations"]),
                _meta=definition["_meta"],
            )
            for definition in tool_definitions(served.surface.executor)
        ]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def handle_call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        served = serve("tools:call")
        executor = served.surface.executor
        step = ToolCall(tool=name, arguments=arguments)
        if served.approvals is not None and served.recorder is not None:
            record, opened = await anyio.to_thread.run_sync(
                served.approvals, executor, step, served.recorder
            )
            if opened is not None:
                request_id, url = opened
                raise UrlElicitationRequiredError(
                    [
                        types.ElicitRequestURLParams(
                            message=f"{name} needs an approver's approval. Open {url}, then "
                            "run the same call again.",
                            url=url,
                            elicitationId=request_id,
                        )
                    ],
                    message=f"Approval needed: open {url}, then run the same call again.",
                )
            result = call_result(executor, record)
        elif confirm_in_client and recorder is not None:
            record = await anyio.to_thread.run_sync(recorder.call, executor, step)
            if record.status == "approval_required":
                record = await confirmed(executor, step, record)
            result = call_result(executor, record)
        else:
            result = await anyio.to_thread.run_sync(
                functools.partial(call_tool, executor, name, arguments, recorder=served.recorder)
            )
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=item["text"]) for item in result["content"]
            ],
            structuredContent=result.get("structuredContent"),
            isError=result["isError"],
        )

    if route is not None or (static is not None and static.surface.resources):

        @server.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_resources() -> list[types.Resource]:
            surface = serve("tools:list").surface
            return [
                types.Resource(**definition)
                for definition in fixed_resources(surface.executor, surface.resources)
            ]

        @server.list_resource_templates()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_templates() -> list[types.ResourceTemplate]:
            surface = serve("tools:list").surface
            return [
                types.ResourceTemplate(**definition)
                for definition in resource_templates(surface.executor, surface.resources)
            ]

        @server.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_read(uri: Any) -> list[ReadResourceContents]:
            served = serve("tools:call")
            try:
                contents = await anyio.to_thread.run_sync(
                    functools.partial(
                        read_resource,
                        served.surface.executor,
                        served.surface.resources,
                        str(uri),
                        recorder=served.recorder,
                    )
                )
            except ResourceReadError as error:
                raise McpError(
                    types.ErrorData(code=types.INVALID_PARAMS, message=str(error))
                ) from None
            return [ReadResourceContents(content=contents["text"], mime_type=contents["mimeType"])]

    if route is not None or (static is not None and static.surface.prompts):

        @server.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_prompts() -> list[types.Prompt]:
            prompts = serve("tools:list").surface.prompts
            return [types.Prompt.model_validate(item) for item in prompt_definitions(prompts)]

        @server.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
        async def handle_prompt(
            prompt: str, arguments: dict[str, str] | None
        ) -> types.GetPromptResult:
            prompts = serve("tools:list").surface.prompts
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
    confirm_in_client: bool = False,
) -> None:
    """Serve the executor's tools (and any resources and prompts) over stdin and stdout."""
    server = build_server(
        executor,
        name=name,
        instructions=instructions,
        recorder=recorder,
        resources=resources,
        prompts=prompts,
        confirm_in_client=confirm_in_client,
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
    confirm_in_client: bool = False,
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
            confirm_in_client=confirm_in_client,
        )
    )
