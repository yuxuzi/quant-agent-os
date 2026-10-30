# 0005: Consequential permissions need a per-call approval

- Status: proposed
- Date: 2026-10-30

## Context

Quant App Factory writes files and runs commands (`uvx pytemplate-uv`, `uv sync`, `pytest`),
which the read-only MVP never needed. Granting `write_local_data` or `execute_code` globally
would let any plan write and run code. Also, the executor's thread timeout cannot stop a running
subprocess.

## Decision

- `write_local_data`, `execute_code`, `network`, and `external_action` are consequential.
  Granting one makes a tool *eligible*. Each call also needs an `Approval` that is bound to the
  run id, the tool name, and the SHA-256 of the validated input, and that is used once.
- When approval is missing, the call records the new status `approval_required` and the run
  pauses in `awaiting_approval`. The model can never create an approval.
- The operator approves through the CLI or SDK, or through the MCP client's confirmation step
  when the operator configures that.
- `execute_code` tools use a subprocess runner. The runner has a fixed working directory, a
  scrubbed environment (no secrets), a timeout, and a hard kill, and it records the command,
  exit code, and output size.
- The read-only default and `READ_ONLY` do not change.

## Consequences

- App Factory gets a plan → approve → apply flow, which matches `pytemplate-uv`'s own
  `plan` / `create` split.
- Every write and every execution has an audit trail.
- Consequential runs have one extra step. That is intended.
