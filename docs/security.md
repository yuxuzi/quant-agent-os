# Security model

Quant-Agent-OS assumes that model output, retrieved documents, and tool output are untrusted.

## Defaults

- **Read-only.** The default policy grants only `read_local_data`. Tools that need `network`,
  `write_local_data`, `execute_code`, or `external_action` are refused unless the operator grants
  those permissions explicitly.
- **Explicit tools.** Only tools registered in a `ToolRegistry` can run, optionally narrowed
  further by an allowlist. There is no dynamic discovery or code loading.
- **Bounded plans.** Plans are validated before execution against the registry, the allowlist,
  and `max_plan_steps`.
- **Bounded calls.** Every call validates its input and output, has a timeout, and has an
  output-size limit. A timed-out Python thread cannot be killed, so tools must avoid unbounded
  or side-effecting work.
- **No feedback into planning.** Tool outputs and retrieved text are not sent back to the
  planner, so injected instructions in data cannot choose tools.
- **Secrets.** Credentials come from the environment or `.env`, are held as `SecretStr`, are
  never displayed, and can be redacted from logs with `RedactSecretsFilter`.

## Hosted embedding providers

Quant Research can embed text with Voyage AI or OpenAI (`--embedder voyage|openai`). This
sends data to that provider:
- **Ingestion** sends every chunk of the corpus. This is an operator command, and the CLI help
  says so.
- **Search** sends the query text. `find_related_signals` then declares the `network`
  permission, so agents and MCP clients can use it only when the operator grants `network`
  (`QUANT_AGENT_OS_GRANTED_PERMISSIONS`). The default policy refuses it.

Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` (or the `QUANT_RESEARCH_`-prefixed
variants) and are held as `SecretStr`. Error messages name the variable, never its value. Use
the offline `hashing` embedder, or none, for documents that must not leave the machine.

## Planned: approvals for consequential permissions

[Decision 0005](decisions/0005-approval-gates.md) (proposed) treats `write_local_data`,
`execute_code`, `network`, and `external_action` as consequential:
- Granting one only makes a tool eligible to run.
- Each call also needs a single-use approval, bound to the run id, the tool name, and the input
  hash. Models can never create approvals.
- `execute_code` tools will run in a subprocess runner with a scrubbed environment, a timeout,
  and a hard kill.
- Knowledge-store ingestion is an operator command, not an agent tool.

## Out of scope for the first release

- autonomous trading or brokerage connectivity;
- unrestricted code execution;
- high-impact external actions;
- hosted multi-tenant operation.

## Reporting a vulnerability

Do not open a public issue. Contact the maintainer privately through the repository's security
advisory feature.
