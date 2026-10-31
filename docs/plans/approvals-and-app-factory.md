# Plan: approval gate (B-09) and App Factory foundation (B-10)

Scope: the last open MVP criterion (5). `create_app` without an approval records
`approval_required`. With an approval, it scaffolds through `pytemplate-uv` 3.0, and the
generated package passes `uv run pytest`, `ruff`, and `mypy`.

The approval gate comes first, because creating an app writes files, runs commands, and
downloads packages ([decision 0005](../decisions/0005-approval-gates.md)).

## Steps

Each step ends with every quality gate passing and a commit.

### A1. Approval gate (core)

- **Consequential permissions.** `CONSEQUENTIAL` is `write_local_data`, `execute_code`,
  `network`, and `external_action`.
  - Granting one makes a tool eligible to run.
  - Each call also needs an `Approval`, bound to the run id, the tool name, and the SHA-256
    of the validated input.
  - Approvals are used once: the `ApprovalLedger` consumes them atomically.
- **Missing approval.** Without one, the call records the new status `approval_required`,
  carrying the input hash, and the run pauses in `awaiting_approval`. No tool code runs.
- **Who approves.** Only the operator, through the SDK (`Platform.approve`, then
  `Platform.resume`) or the CLI. No tool can create an approval, and none is exposed over MCP.
- **Standing approvals.** `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS` lets an operator approve a
  consequential permission once for every call, for example `network` when serving with a
  hosted embedder. It is empty by default, and the audit trail records each use.
- **Unchanged:** `READ_ONLY` and the default grants. Read-only tools are unaffected.

### A2. Subprocess runner (core)

`run_command` in `quant_agent_os.runtime.runner` runs a command with:
- a fixed working directory;
- a scrubbed environment: an allowlist of `PATH`, home, temp, locale, and `uv` cache
  variables, so no secrets pass through;
- a timeout with a hard kill of the whole process tree;
- a bounded capture of the output.

It returns the command, exit code, duration, output size, and an output tail, and it never
raises for a failed command.

### A3. App Factory domain and planner

- **`AppSpec`:** the name, kind (`agent-tool`, `python-cli`, `fastapi-service`), description,
  author, Python version, components, and, for `agent-tool`, the tool name. Specs are TOML
  files.
- **`ArchitecturePlanner`:** deterministic. It maps a spec to a `ScaffoldRequest` (profile,
  components, template answers, template pack) and gives the reasons for each choice.
- **`ScaffoldProvider`:** a protocol with `plan`, which writes nothing, and `create`.

### A4. `PytemplateUvScaffolder` and the `agent-tool` pack

- **Adapter.** It writes the request as TOML and runs
  `uvx pytemplate-uv==3.0.* {plan|create-from-config} <file> --json` through the runner.
  - Exit code 1 maps to a request error and exit code 2 to a render error.
  - It parses the JSON result.
  - It never imports `pytemplate-uv`.
- **The `agent-tool` template pack** ships inside `quant_app_factory`. Its generated package:
  - has one example tool and a registry;
  - has tests that run the platform's contract checks (`quant_agent_os.testing.check_tool`): a
    complete descriptor, read-only permissions, a direct call equal to an MCP call, and
    invalid input recorded as such;
  - depends on `quant-agent-os` from its git repository, or from a local path for offline
    checks.
- `python-cli` and `fastapi-service` use pytemplate-uv's bundled profiles and do not depend
  on Quant-Agent-OS.

### A5. Tools, validation, and CLI

- **`plan_app`** is read-only. It returns the planned scaffold request, the reasons, and the
  exact `create_app` input to approve.
- **`create_app`** declares `write_local_data`, `execute_code`, and `network`. It:
  - scaffolds the project;
  - runs the generated project's lifecycle commands (`uv sync`, ruff check, ruff format
    check, mypy, pytest) through the runner;
  - returns a `FactoryReport`: files, checks, versions, and the request.
- **CLI:**
  - `quant-factory plan SPEC.toml` previews the exact files through pytemplate-uv's `plan`;
  - `quant-factory create SPEC.toml` shows the plan, asks the operator, and records the
    approval and the run.

### A6. Tests and docs

- **Offline tests:**
  - recorded pytemplate-uv JSON fixtures, through a fake runner;
  - the approval lifecycle;
  - a real subprocess-runner test (timeout kill, scrubbed environment);
  - the planner rules.
- **Opt-in smoke test** (`QUANT_FACTORY_SMOKE=1`): real `uvx`, a real scaffold of each kind,
  and the generated checks.
- **Docs:** decision 0005 accepted, the walkthrough's App Factory section, and the MVP status.

## Results (2026-10-30)

| Check | Result |
| --- | --- |
| `create_app` without an approval | `approval_required`; nothing is scaffolded or run (offline test) |
| `create_app` after `approve` and `resume` | Scaffolds once; the approval is consumed, and a replay needs a new one |
| Real `agent-tool` project (pytemplate-uv 3.0.x, this checkout as its source) | `uv sync`, ruff check, ruff format check, mypy, and pytest (4 tests, including `check_tool`) pass |
| Real `python-cli` project | All five checks pass |
| `agent-tool` with the default git source | Installs. Its tests need `quant_agent_os.testing`, which reaches the git repository when this branch merges |
| Runner | A timeout kills a grandchild process; secrets and package-index URLs are not passed through |

Deviation from the step text above:
- **`plan_app`** is the deterministic planner only, so it stays read-only. The exact file list
  comes from pytemplate-uv's `plan`, which runs a program, so it is an operator command
  (`quant-factory plan`), not an agent tool.

## Out of scope

- AppSpec drafting by a model (B-16).
- Code generation beyond scaffolding, and the fix loop (B-20).
- Approvals through an MCP client's confirmation step.
- Publishing the template pack separately.
