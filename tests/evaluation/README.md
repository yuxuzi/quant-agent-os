# Evaluation suites

Evaluations measure quality, not just correctness. Milestones add them as they land:

- **Q4, research retrieval:** citation accuracy on a fixture corpus with a recorded baseline,
  plus prompt-injection fixtures that must not change the plan or tool selection.
- **Q6, orchestration:** plan validity and report completeness over a fixed set of requests,
  run with the fake provider by default and with a real provider only when explicitly enabled.

Evaluations that call a real model must be opt-in (for example `QUANT_AGENT_OS_EVAL_LIVE=1`) and
must never run in default CI.
