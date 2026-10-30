"""MCP boundary: adapters over the tool registry and a stdio transport (no business logic).

The transport needs the optional ``mcp`` extra; importing this package does not.
"""

from quant_agent_os.mcp.adapters import call_tool, tool_definitions
from quant_agent_os.mcp.server import MCPUnavailableError, build_server, run_stdio, serve_stdio

__all__ = [
    "MCPUnavailableError",
    "build_server",
    "call_tool",
    "run_stdio",
    "serve_stdio",
    "tool_definitions",
]
