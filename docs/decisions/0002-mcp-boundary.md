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

## Consequences

- MCP and direct calls produce identical results; contract tests assert this.
- Permissions, validation, timeouts, and output limits apply on every path.
- Adding the server transport later does not change tools or tests.
