"""MCP boundary: adapters over the tool registry and a stdio transport (no business logic).

The transport needs the optional ``mcp`` extra; importing this package does not. MCP over HTTP
(`quant_agent_os.mcp.http`) also needs the ``api`` extra.
"""

from quant_agent_os.mcp.adapters import (
    ResourceReadError,
    call_tool,
    compact_schema,
    fixed_resources,
    get_prompt,
    prompt_definitions,
    read_resource,
    resource_templates,
    served_resources,
    tool_definitions,
)
from quant_agent_os.mcp.server import (
    AccessDeniedError,
    MCPUnavailableError,
    build_server,
    run_stdio,
    serve_stdio,
)

__all__ = [
    "AccessDeniedError",
    "MCPUnavailableError",
    "ResourceReadError",
    "build_server",
    "call_tool",
    "compact_schema",
    "fixed_resources",
    "get_prompt",
    "prompt_definitions",
    "read_resource",
    "resource_templates",
    "run_stdio",
    "serve_stdio",
    "served_resources",
    "tool_definitions",
]
