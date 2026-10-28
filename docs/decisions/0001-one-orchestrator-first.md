# 0001: One bounded orchestrator before any multi-agent design

- Status: accepted
- Date: 2026-10-28

## Context

The QuantAgentOS proposal lists possible specialist agents (research, coding, backtest,
reporting). The MVP has to be buildable by one developer and must be auditable and testable.

## Decision

The first release has exactly one orchestrator. It:
- asks the provider for a plan;
- validates the plan against the registry, the allowlist, and a step limit;
- runs each step through the bounded executor;
- builds a structured report.

Specialist agents are added only when an evaluation shows a measurable gain over this baseline.

## Consequences

- A single plan is easy to log, replay with a fake provider, and test.
- There are fewer prompt-injection and failure surfaces than with agents passing work to each
  other.
- Some multi-step research needs several runs until evaluations justify more autonomy.
