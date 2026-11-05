# Evaluation suites

Evaluations measure quality, not just correctness. Milestones add them as they land:

- **Q4, research retrieval:** citation accuracy on a fixture corpus with a recorded baseline,
  plus prompt-injection fixtures that must not change what a run calls.
- **P1.1, agent efficiency** (`agent_efficiency.py`): what a client pays to do typical tasks
  over the MCP servers. Per server: tools, definition bytes, and instruction bytes the model sees
  every turn. Per task: calls, argument bytes, result bytes, and success. A scripted client
  drives in-memory MCP sessions, so it is offline and deterministic and runs in the default
  suite, which fails if a task fails or a metric grows past `agent_efficiency_baseline.json`.
  Run `uv run python tests/evaluation/agent_efficiency.py --update` to rebaseline after an
  intended change, and report the delta in the PR.

Evaluations that call a real model must be opt-in (for example `QUANT_AGENT_OS_EVAL_LIVE=1`) and
must never run in default CI.
