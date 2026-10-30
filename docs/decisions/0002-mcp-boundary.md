# 0002: MCP is an adapter over the registry, never a home for logic

- Status: accepted
- Date: 2026-10-28

## Context

MCP is the interoperability boundary for exposing tools to other agents and clients. Putting
business logic in MCP servers would duplicate code and bypass the tool contract.

## Decision

- Core logic lives in plain Python functions registered as `ToolSpec`s.
- `quant_agent_os.mcp` maps the registry to MCP `tools/list` definitions and executes `tools/call`
  through the same `ToolExecutor` the orchestrator uses.
- The adapter has no SDK dependency; a transport (milestone Q5) only forwards requests.
- Direct in-process calls stay available.

## Implementation note (2026-10-30)

`quant_agent_os.mcp.server` is the transport, built on the official MCP Python SDK (the
optional `mcp` extra). Its `tools/list` handler returns `tool_definitions` and its
`tools/call` handler returns `call_tool`, run on a worker thread. SDK input validation is off,
so the executor's pydantic validation produces the same `invalid_input` errors as a direct
call. Contract tests compare an in-memory MCP session with direct calls, and an end-to-end test
drives `quant-research serve-mcp` over stdio. Products wire their own registries into it, so
Core still imports no product.

## Consequences

- MCP and direct calls produce identical results; contract tests assert this.
- Permissions, validation, timeouts, and output limits apply on every path.
- Adding the server transport later does not change tools or tests.
