# Architecture

QuantAgentOS is the runtime layer of QuantFoundry. It registers and orchestrates capabilities
that live in ordinary, independently tested Python packages. It does not contain financial
analytics or retrieval logic itself.

## Component boundaries

| Component | Responsibility | Depends on |
| --- | --- | --- |
| Portfolio analytics (separate package) | Deterministic metrics, usable without an LLM | Numerical libraries |
| Quant Search (separate package) | Retrieval with source identifiers and locations | User-supplied documents and stores |
| `quant_agent_os.tools` | Tool contract, registry, permissions | pydantic |
| `quant_agent_os.runtime` | Plan models, bounded executor, orchestrator | tools, providers, reporting |
| `quant_agent_os.providers` | Model-provider interface and adapters | — |
| `quant_agent_os.evidence`, `reporting` | Evidence, provenance, structured reports | runtime data models |
| `quant_agent_os.mcp` | MCP tool definitions and calls over the registry | runtime executor |
| Quant AI Assistant (`apps/`) | User-facing application | quant-agent-os |
| uv-template (`pytemplate-uv`) | Generates apps and tool packages | — |

Dependencies point downward only. Inside the package the import layering is:
`tools` → `runtime.plan` / `runtime.executor` → `evidence` / `reporting` →
`runtime.orchestrator` → `cli`.

## Request lifecycle

1. **Plan.** The orchestrator sends the provider the goal and the descriptors of allowed tools,
   then parses the response into a `Plan`.
2. **Validate.** The plan is rejected before anything runs if it is malformed, exceeds
   `max_steps`, or names a tool that is unregistered or outside the allowlist.
3. **Execute.** For each step, `ToolExecutor`:
   - checks permissions;
   - validates input against the tool's input model;
   - runs the plain function with a timeout;
   - validates the output and enforces its size limit.

   Failures become records, not exceptions.
4. **Report.** `build_report` collects `evidence` and `warnings` from tool outputs, turns failed
   calls into warnings, and adds standard limitations and run metadata.

Tool outputs and retrieved text are never fed back into planning. This is the first defense
against prompt injection: data cannot change which tools run.

## The tool contract

A tool is a `ToolSpec` with:
- a snake_case name and a description;
- a version;
- pydantic input and output models;
- the plain function;
- declared `Permission`s;
- `timeout_seconds` and `max_output_bytes`.

The `@tool` decorator builds one from a typed function, which stays directly callable. Output
models may expose `warnings: list[str]` and `evidence: list[Evidence]`; reports collect both.

## Extension points

- **Models:** implement `ModelProvider` (`name`, `generate(prompt) -> str`). Ship each vendor as
  an adapter with its own optional dependency, and keep tests on `FakeModelProvider`.
- **Tools:** register `ToolSpec`s from your own packages in an explicit `ToolRegistry`. There is
  no plugin discovery or dynamic code loading.
- **MCP:** a server transport only forwards `tools/list` and `tools/call` to
  `quant_agent_os.mcp.tool_definitions` and `call_tool`.

## Not in scope for the MVP

Multi-agent orchestration, autonomous trading or external actions, unrestricted code execution,
hosted multi-tenant infrastructure, mandatory vector databases, and broad connector coverage.
See [decision 0001](decisions/0001-one-orchestrator-first.md) and the
[engineering plan](ENGINEERING_REVIEW_PLAN.md).
