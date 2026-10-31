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
  permission. It runs only when the operator grants `network`
  (`QUANT_AGENT_OS_GRANTED_PERMISSIONS`) and either approves each call or pre-approves
  `network` (`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`). The default policy refuses it.

Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` (or the `QUANT_RESEARCH_`-prefixed
variants) and are held as `SecretStr`. Error messages name the variable, never its value. Use
the offline `hashing` embedder, or none, for documents that must not leave the machine.

## Serving is read-only

`quant-research serve-mcp` and `quant-research search` open the store with SQLite's
`mode=ro`. They create, migrate, and write nothing, so a read-only mount works. A store at
another schema version is refused with the command that upgrades it. Each answer reads inside
one transaction, so it never mixes two versions of the corpus. Hosted query embedding is
bounded to 8 seconds inside the 15-second tool timeout; past that, the vector channel is
skipped with a warning. Repeated queries are served from a cache rather than sent to the
provider again.

## Run records

`serve-mcp` records every call as a run in `runs.db` beside the knowledge store. Pass
`--runs PATH` to put it elsewhere, or `--no-record` to keep none. The SDK `Platform` keeps runs
in memory unless it is given a run store. What each part of a record holds:
- **Calls.** A call record holds the tool's arguments and output, which for research tools
  includes retrieved passages. Treat `runs.db` as being as sensitive as the corpus.
- **Redaction.** Before anything is stored, `SecretStr` values and values under keys that
  name a secret (`api_key`, `token`, `password`, ...) are replaced with `[REDACTED]`.
- **Audit events.** These hold only ids, hashes, and statuses, and the database rejects any
  update or deletion of them.
- **Store failures.** If the run store cannot be opened, serving continues unrecorded with a
  warning, and a failed write is logged. Losing an answer the tool already produced would not
  make the record more complete.

## Approvals for consequential permissions

[Decision 0005](decisions/0005-approval-gates.md) treats `write_local_data`, `execute_code`,
`network`, and `external_action` as consequential:
- **Granting is only eligibility.** Granting one of these permissions makes a tool eligible to
  run, nothing more.
- **Each call needs an approval.** The approval is single-use and bound to the run id, the tool
  name, and the input hash.
  - Without one, the call records `approval_required`, runs nothing, and the run waits in
    `awaiting_approval`.
  - The operator approves through the SDK (`Platform.approve`, then `Platform.resume`) or by
    confirming `quant-factory create`.
  - Models and MCP clients can never create approvals.
- **Standing approvals.** An operator can pre-approve a permission for every call with
  `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`. It is empty by default, and every call that uses
  it records `standing:<permission>`.
- **Running programs.** Programs run through `run_command`:
  - a fixed working directory and no stdin;
  - an allowlisted environment: `PATH`, home, temp, locale, uv caches, and certificates. API
    keys, `QUANT_AGENT_OS_*` settings, and package-index URLs (which can embed credentials)
    never pass through;
  - a timeout that kills the whole process tree.
- **App Factory checks.** The App Factory runs only `uv` lifecycle commands in a generated
  project. Any other program is reported and skipped.
- **Ingestion.** Knowledge-store ingestion is an operator command, not an agent tool.

## Out of scope for the first release

- autonomous trading or brokerage connectivity;
- unrestricted code execution;
- high-impact external actions;
- hosted multi-tenant operation.

## Reporting a vulnerability

Do not open a public issue. Contact the maintainer privately through the repository's security
advisory feature.
