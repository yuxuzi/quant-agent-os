# 0016: Clients orchestrate; the platform has no planner

- Status: accepted
- Date: 2026-11-04
- Supersedes: [0001](0001-one-orchestrator-first.md)

## Context

Decision 0001 made one bounded orchestrator the first design: a model proposes a plan, and the
platform validates and runs it. [Agent surface](../plans/archive/agent-surface.md) then moved planning to
the client, and the orchestrator stayed only as an SDK feature.

The only provider ever built is `FakeModelProvider`, so no user could run the orchestrator with a
model. Its settings (`model_provider`, `model_name`, `api_key`, `max_plan_steps`) did nothing, and
a user could set an API key expecting an effect. Four backlog items (B-12, B-15, B-16, and B-20)
sat blocked on a provider choice that no user had asked for. It also planned by parsing free-text
JSON, where model vendors now offer native tool use.

## Decision

The platform has no planner and calls no model.
- `Orchestrator`, `Platform.run`, `ModelProvider`, `FakeModelProvider`, `Plan`, the
  `plan-schema` command, and the model settings are removed.
- The executor takes a plain `ToolCall`.
- `Platform.report(goal, calls)` keeps what had value without a model: named calls run as one
  recorded run, ending in an evidence-linked report.
- Someone driving the tools with their own model uses the client's or vendor's tool loop over
  `tool_definitions` and `Platform.call`, as `examples/your-own-model/` shows.
- B-12, B-15, B-16, and B-20 are closed until a design partner asks for them.

## Consequences

- There is one way to orchestrate, and the invariants get simpler: nothing on the server reads
  tool output to decide what to do next, because nothing on the server decides.
- Retrieved text is still untrusted. It remains marked as such for clients, and security.md still
  says that clients must not follow it. The platform cannot enforce that in a client.
- Server-side synthesis and model-drafted specs need a new decision if they return.
- Records of old runs keep `kind = "plan"` and their `provider`, so stored runs stay readable.
- Multi-agent design remains out of scope, with no orchestrator to build it on.
