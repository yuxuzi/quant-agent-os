"""MCP boundary: adapters over the tool registry and a stdio transport (no business logic).

The transport needs the optional ``mcp`` extra; importing this package does not.
"""

from quant_agent_os.mcp.adapters import (
    ResourceReadError,
    call_tool,
    compact_schema,
    get_prompt,
    prompt_definitions,
    read_resource,
    resource_templates,
    served_resources,
    tool_definitions,
)
from quant_agent_os.mcp.server import MCPUnavailableError, build_server, run_stdio, serve_stdio

__all__ = [
    "MCPUnavailableError",
    "ResourceReadError",
    "build_server",
    "call_tool",
    "compact_schema",
    "get_prompt",
    "prompt_definitions",
    "read_resource",
    "resource_templates",
    "run_stdio",
    "serve_stdio",
    "served_resources",
    "tool_definitions",
]
