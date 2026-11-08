# Plan: 0.3.0, ready for a first pilot

## Context

The next phase is one firm, hosting its own server, running one workflow: AI-assisted portfolio
research and risk analysis over the firm's own research, data, and Python tools. Every step below
exists because a pilot needs it. Anything else waits for a named design partner.

**Baseline, measured 2026-11-06 on `e99a94a`:** 1003 tests pass and 21 skip, and lint, format, and
strict types are clean. The last tag is v0.2.0, and P3 to P6 of the simplification plan are unreleased.

### What a pilot needs, against what exists

| A pilot needs | Today | Evidence | Stage |
| --- | --- | --- | --- |
| Serve the firm's own Python tools | **Missing.** The SDK can call them, but no server can serve them | `Toolset` is a closed `Literal` and `compose()` takes no tools, `quant_platform/composition.py:33,84`; `_serve_http` is private | S1 |
| Decide which user may call which tool | **Missing.** One server-wide policy; any `tools:call` key reaches every tool | `PermissionPolicy.check(tool, required)` takes no caller, `tools/permissions.py:58` | S2 |
| Decide which user may read which dataset | **Missing.** The tenant is the only boundary | The caller stops at `RunRecorder`; `ToolExecutor.execute(step, run_id)` never sees it, `runtime/executor.py:182` | S2 |
| Reject an unauthorized request, and show it | Partial. 401 and 403 by key and scope, nothing per resource | `api/app.py`, `mcp/http.py` | S2, S3 |
| One repeatable demonstration, end to end | **Missing.** The portfolio example runs two toy tools with no server, auth, or research | `examples/portfolio-research/` | S3 |
| Clean install | Good. Docker, two variants, smoke test in CI | `deploy/`, `docs/deployment.md` | S4 |
| Upgrade, rollback, backup, restore | **Missing.** One sentence on backups. An older binary refuses a newer store, so a rollback needs a restore that nothing documents | `docs/deployment.md` section 7, `docs/releasing.md` | S4 |
| Compare numbers and answers to agreed test cases | Internal only | `tests/research/real_corpus.py` and `research_eval.py` score fixed sets | S5 |
| Abstain visibly | Works, implicitly. 28 of 28 off-topic questions abstain on real papers, but abstaining is only an empty `passages` | `EvidencePacket`, `application/retrieval.py:105` | S5 |
| Audit fields, in the firm's own systems | Partial. Principal, tool version, input hash, and data versions are recorded; `qaos runs` only lists and shows | `quant_agent_os/cli.py:155` | S5 |
| Data retention | **Missing.** Runs are kept forever, and they can hold retrieved passages | No prune or retention code in `runs/` | S5 |
| Security boundaries and responsibilities | Partial. Thorough on controls, with no responsibility split or data classification | `docs/security.md`, `docs/security-review.md` | S6 |
| Sign-in through the firm's identity provider | Exists as a library, served by no command | `quant_platform/identity/oauth.py`, decision 0023 | Conditional, see below |

Already enough, with no work planned: typed contracts, bounded execution, MCP, REST, SDK, read-only
defaults, evidence labels, performance and drawdown metrics, volatility decomposition, factor
exposures, and read-only PostgreSQL.

### Bugs found while surveying

- `docs/deployment.md` step 4 teaches `datasets push`. That command is `qaos data add`, so the step fails
  as written. The doc test does not check compose service arguments. (S4.1)
- `docs/deployment.md` section 7 says quotas "come with plan step P7", which is finished and now hosted.
- The compose `usage` service runs `qaos hosted`, so a single-firm server depends on the hosted group.

### Not in 0.3.0

- **Transaction cost analysis and attribution.** Two or three tested tools make a pilot, and those exist.
  Build them when a partner asks.
- **Market-data connectors, `run_backtest`, more loaders, any model provider.** They stay under
  "Waiting for a named design partner". S1 lets a firm connect its own data.
- **Per-document access to the corpus.** Decision 0013 chose separate stores because a missed filter in
  full-text, vector, or graph search is a leak. A firm that needs this runs one server per corpus, and S6
  says so.
- **New tenant features.** Decision 0023 removed the multi-tenant server. App Factory work resumed with decision 0022.
- **Licence-key enforcement in code.** Terms belong in a contract. A check in an MIT or source-available
  codebase is removable, and it would be one more thing to fail on a customer's server.

**Conditional stage: OAuth sign-in.** The token checks are in `quant_platform.identity`, served by no command (decision
0023). If the first pilot's client needs a sign-in connector, wiring them into `qaos serve` comes right after S2, which
defines the caller it maps onto, and it needs the owner's approval as a security change.

## How to deliver it

One commit per step and one PR per stage, stacked if the previous PR is not merged. Run test, lint, format,
and type checks on a fresh clone before each push, and report CI per OS and Python. A stage that changes
the tool surface reports its agent-efficiency delta. Stages run in order, because S3 demonstrates S1 and
S2. Sizes are relative (S, M, L).

**S1 to S3 come first.** They are what a firm needs to see. S4 to S6 can finish while a first pilot is under
way, so that what it actually needs decides their details, except that S4.1 and the security document should be
ready before a firm's technical reviewer reads them.

**If time runs short,** cut in this order: S5.2 (retention), S5.1 (the evaluation command, run the scripts
by hand), S4.3 (the automated drill, run it by hand). S1 to S3 are the product and are not cut.

## S0: Decisions before code (gate)

No code. Each is the owner's. S1 does not start until the first is settled, since whatever merges to the
public `main` stays under the licence it merged under.

| # | Decision | Why now | Recommendation |
| --- | --- | --- | --- |
| 1 | **O-4: the licence from 0.3.0 onwards** | **Decided:** the Business Source License 1.1, [decision 0019](../decisions/0019-business-source-license.md), applied in its own PR | Counsel reviews it before first commercial use |
| 2 | **Name availability (B-22)** | The README still says the name is unchecked, and a pilot is commercial use | Check before S3's demonstration is shown to anyone |
| 3 | **S1's and S2's design** | **Approved 2026-11-06,** including `firm_server.py` over a `--tools-module` option. S1 is recorded as decision 0020, and S2 as decision 0021 | Build as written below |

## S1: Serve a firm's own tools (M)

The firm writes a small `firm_server.py` that imports its tools and hands them to the platform. This keeps
the invariant: registration stays explicit, in code the firm owns, with no module path in config and no
discovery. A `--tools-module` option was rejected because it is dynamic code loading.

| Step | Work |
| --- | --- |
| S1.1 | `compose(..., extra_tools=..., extra_instructions=...)`. `Catalog.toolsets` labels them `firm`. A name clash with a platform tool is an error at start-up, not an override |
| S1.2 | Public `serve_http(catalog, ...)` and `serve_stdio(catalog, ...)` in `quant_platform`, taken from the private functions in `cli.py`, which then call them. Same safe defaults: no start without a key or a run store, loopback by default |
| S1.3 | A firm tool may pass a custom `DatasetStore`, so one agreed data source needs no connector from us. Document it with one tested example |
| S1.4 | `examples/firm-tools/`: one tool, `firm_server.py`, a `check_tool` test, and a Dockerfile `FROM` the platform image |
| S1.5 | `deployment.md` section "Add your own tools"; decision 0020 |

**Exit:** the example's tool is served over MCP and REST, with results equal to a direct call. It is refused
under the default policy when it declares a permission, and it appears in `qaos runs` with its version. `tests/server/test_skills.py` still
passes, since skills cover only the platform's own tools. No change to the platform's agent-efficiency numbers.

## S2: Caller-aware authorization (L)

| Step | Work |
| --- | --- |
| S2.1 | `Caller(principal, tenant, groups)` in Core, moved from `mcp/http.py`. Keys gain optional `groups`; `qaos keys new --group`. REST and HTTP MCP bind the caller once per request, and the executor carries it onto the tool's thread. stdio and the SDK bind none: they trust whoever started them, as now |
| S2.2 | An `AccessPolicy` port in Core: `allows(caller, action, resource) -> bool`, with actions `tool.call` and `dataset.read`. With no policy configured, behaviour is exactly as today |
| S2.3 | Tool-level check in the executor, after the existing policy check. A refusal records `denied` with the principal and the audit event. `tools/list` and `GET /v1/tools` show a caller only what it may call |
| S2.4 | Dataset-level check at the read: a `GuardedDatasetStore` around the store, reading the caller from a `ContextVar`. `ids()` filters; `get()` refuses. Runs through `copy_context()` at `self._pool.submit`, because tools run on a pool thread, and `research_report`'s nested calls must keep the caller |
| S2.5 | A refused dataset and an unknown one answer identically. `_unknown_dataset` lists known ids today, so it must list only permitted ones, or it discloses them |
| S2.6 | `FileAccessPolicy`: an `access.toml` mapping groups to tool and dataset patterns. Deny unless listed. Reloaded on change like the keys file, and **while it is missing or invalid, nothing is allowed**. A firm's entitlement service implements the same port in `firm_server.py` |
| S2.7 | `current_caller()` for firm tools, which enforce their own resources (a portfolio or an account) |
| S2.8 | `tests/contract/test_authorization.py`: a matrix of caller, tool, and dataset over MCP and REST. Include resources, `research_report`'s nested reads, `list_datasets`, the error text, and the audit record. Marked as a release gate, like the isolation tests |

**Security notes.** No default widens. `runs:read` still reads every caller's runs, including what they
retrieved and computed, so S6 says to grant it only to auditors. Approvals are untouched. The corpus is
all-or-nothing per caller, through tool-level rules.

**Exit:** the matrix passes on both interfaces; the policy file's failure mode is tested;
the concurrency path is tested with parallel callers with different entitlements, because a leaked
`ContextVar` would be a silent cross-user read. Report the efficiency delta (`tools/list` output is unchanged
with no policy).

## S3: The Portfolio Intelligence demonstration (M)

`examples/portfolio-intelligence/`, entirely fictional: a portfolio's weights, asset and factor returns as
datasets, a few synthetic research notes, one firm tool from S1, and two users from S2.

| Step | Work |
| --- | --- |
| S3.1 | The fixture, with risk figures checked against an independent numpy calculation in a known-answer test. Replaces `examples/portfolio-research/` if nothing else needs it, updating its doc and test references |
| S3.2 | `demo.py`: starts a real HTTP server on a temporary home and drives it over MCP with no model, one printed step each: discover tools; authenticate; compute risk and exposures by reference; retrieve cited research; build the evidence-linked report; refuse an unauthorized dataset; ask something the corpus cannot answer and abstain; show the run and its audit trail |
| S3.3 | `tests/integration/test_portfolio_demo.py` runs it offline in default CI, and asserts each step |
| S3.4 | `docs/demo.md`: the same story with a real client, a presenter's script of at most 10 minutes, and the prompts to use. It says which output comes from the client's model and which from the tools. Linked from getting started |

`examples/portfolio-research/` stays: six test files and the tutorial use its tools, so S3.1 adds the demonstration beside it.

**Exit:** 20 consecutive clean runs on a fresh clone; a timed run with Claude under 10 minutes; a reader of
`docs/demo.md` can run it with no other help.

## S4: Customer-hosted install, upgrade, and rollback (M)

| Step | Work |
| --- | --- |
| S4.1 | Fix `deployment.md` (the bugs above). Extend the doc test to compose service arguments, so a wrong subcommand fails. Make usage export work without `qaos hosted`, or drop the service from the default compose file |
| S4.2 | `docs/operations.md`: configuration and secrets, backup and restore per volume, upgrade, rollback, and what to do when a store refuses to open. Rollback is stated plainly: restore the backup taken before the upgrade |
| S4.3 | `deploy/upgrade-test.sh`: v0.2.0 image, seed it, back up, upgrade to this commit, confirm old runs and answers survive; roll back, restore, confirm again. For SQLite and PostgreSQL. It uses a real tagged release, the point of doing it in this release |
| S4.4 | A `doctor` compose service, so `qaos doctor` checks the deployed volumes. Its output goes in support tickets, so it must hold no secrets (a test checks) |
| S4.5 | Air-gapped install: build at the customer's tag, then `docker save` and `docker load`. Document what needs network and when. Publishing an image to a registry is a CI change and is not assumed |

The default compose file drops the `usage` service: usage export stays a hosted feature, and S5.4 gives a firm its
audit export. `deploy/smoke-test.sh` proves `doctor`, backup, and restore on every pull request. `deploy/upgrade-test.sh`
runs in its own workflow, on release tags and on pull requests that change the deploy files or the stores.

**Exit:** someone following only `deployment.md` and `operations.md` installs on a clean machine with both
variants, then upgrades and rolls back, with no undocumented step. Note where they got stuck and fix the doc.

## S5: Acceptance evaluation and audit export (M)

| Step | Work |
| --- | --- |
| S5.1 | `qaos eval --gold cases.toml`: an operator command, not an agent tool. Numeric cases name a tool, arguments, an expected value, and a tolerance. Retrieval cases name a question and either the expected source or `abstain`. Output is a pass or fail report, in Markdown and JSON, to attach to acceptance. Reuses the scoring in `tests/research/` by moving what is shared into `quant_research` |
| S5.2 | `qaos runs prune --older-than` with a recorded audit event, and retention behaviour documented. Off by default: nothing deletes without an operator asking |
| S5.3 | `abstained: bool` on `EvidencePacket`, true when no passage qualifies, so a client and an acceptance test need not infer it. A tool schema change: bump the version, the tool lock, the tools changelog, and report the efficiency delta |
| S5.4 | `qaos runs export --from --to --format jsonl`, redacted as stored, so a firm can send it to its own log system |

`qaos eval` runs cases through the served tools and shares no code with `tests/research`: that scoring gives
aggregate metrics, and an acceptance case is a verdict, so there was nothing worth moving. `qaos runs export` leaves out
arguments and outputs rather than exporting them redacted, because a redacted output can still hold passages.

**Exit:** the demo's fixture passes `qaos eval`. A deliberately wrong expected value and an answerable question
marked `abstain` each fail it. An export round-trips every agreed audit field: principal, tool and version, input hash, data versions, status, time.

## S6: Security document and positioning (S)

| Step | Work |
| --- | --- |
| S6.1 | `docs/security.md` gains: a trust-boundary diagram; a table of who is responsible for what, platform or firm (TLS, identity, secrets and rotation, backups, network, patching, incidents); data classification and where each kind is stored, including that runs can hold passages; the limits from S2. No claim of certification or compliance |
| S6.2 | Refresh `security-review.md` against S1 and S2. Its status stays "an AI agent's review, not a penetration test". An independent review remains a recommendation, before production use |
| S6.3 | README leads with connecting the firm's own tools and research under its control. Analytics reads as workflows on the platform, and App Factory moves below the fold as optional. No retrieval claim beyond what is measured: the "Say what was measured" invariant holds. `AGENTS.md` and `docs/products/` follow |

**Exit:** the doc tests pass. A technical reader unfamiliar with the project can say from the README alone what it does and what the firm operates.

## S7: Release 0.3.0 (S, gated)

Follow `docs/releasing.md`, and **ask first**, because a version bump and tag are the owner's call.

- [ ] CI green on every OS and Python; PostgreSQL store tests run locally; authorization and isolation gates pass
- [ ] Agent-efficiency evaluation shows only intended deltas; research evaluation and `real_corpus.py eval` hold their baselines
- [ ] Licence files match decision 0019, and counsel has reviewed them
- [ ] Upgrade drill passes from v0.2.0
- [ ] Install from the tag in a clean home, with no `--python` override
- [ ] Changelog and tools changelog updated, and the `@main` note removed from getting started
- [ ] `ENGINEERING_REVIEW_PLAN.md` reduced to what is open, and this plan archived

## Deferred to after the first pilot

Learn these from a real deployment first: reusable entitlement adapters, per-document access, dataset limits
(20 MB, 200,000 rows, 500 columns), OAuth split if not already needed, a support bundle, a published image,
and running slow tools in a killable process (F-2).
