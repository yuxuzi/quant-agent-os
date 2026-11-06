# Agent surface: skills plus a remote MCP platform

**Scope.** This plan covers how third-party AI clients (Claude, Copilot, Cursor, and agents built
on their SDKs) use Quant-Agent-OS, and the work to make that use efficient, safe, and ready to
sell as a hosted service. I reviewed the MCP adapter and transport, the SDK facade, the REST
interface (decision 0011), the approval gate (decision 0005), and the product servers, as of
2026-11-01.

**Verdict.** The core is sound: one `ToolExecutor` behind MCP, REST, and the SDK, with tests
that prove they agree; typed contracts; per-call approvals bound to the input hash; evidence
that keeps facts and interpretation apart. The agent-facing surface lags current practice. MCP
runs on stdio only, so the team server cannot be added to Claude or Cursor as a remote server.
Clients see bare tools with no output schemas, resources, prompts, or workflow guidance. The
analytics tools are not served at all. Numbers travel through the model as inline arrays, and
approvals stop at a dead end inside the client.

**Target.** Quant-Agent-OS is a platform. It is one **remote MCP server**, organised into
toolsets, together with a set of **skills** that teach clients how to use it. MCP holds the
capabilities and enforces policy. Skills hold the know-how: which tool to call when, the
conventions, and how to cite. The platform runs no agents of its own. A workflow is either a
skill, when the client composes the calls, or a deterministic composite tool, when the
platform composes them. The server is designed so that a hosted, multi-tenant, metered
deployment can be offered later without changing tools or skills.

## Findings

### A. Reach (who can use it)

| # | Gap | Evidence | Why it matters |
| --- | --- | --- | --- |
| A1 | **MCP over stdio only.** The team server speaks REST with API keys, not MCP. | `mcp/server.py` has only `serve_stdio`; `deploy/` serves `serve-api`. | A hosted or team deployment cannot be added as a remote MCP server or connector. Without that there is no hosted product. |
| A2 | **Analytics is not served.** No CLI serves `quant_analytics.tools`; only `examples/portfolio-research` imports it. | `grep quant_analytics packages/*/src` finds nothing. | Deterministic computed evidence, the most trustworthy kind, cannot be reached by MCP clients. |
| A3 | **One server per product.** `quant-research serve-mcp` and `quant-factory serve-mcp` are configured separately. | Two `serve-mcp` commands. | Two installs and two auth set-ups. A hosted product needs one endpoint. |
| A4 | **No path to approve in the client.** `create_app` and `check_app` are left off the factory server because clients cannot approve. | `quant_app_factory/cli.py` `serve_mcp` docstring; decision 0005 note. | Consequential capabilities are unreachable over MCP. |

### B. Efficiency (tokens, calls, correctness)

| # | Gap | Evidence | Why it matters |
| --- | --- | --- | --- |
| B1 | **Numbers pass through the model.** `analyze_performance` takes `returns: list[float]`. | `quant_analytics/tools.py` `ReturnsInput`. | Ten years of daily returns is about 2,500 numbers the model must copy without error: costly and error-prone. A remote server also cannot read the user's files. |
| B2 | **No `outputSchema`.** Every `ToolSpec` has an `output_model`, but `tool_definitions` sends only `inputSchema`. | `mcp/adapters.py`. | Clients cannot validate or plan around typed results. |
| B3 | **Large default outputs.** Search returns up to 200 KB, and there is no concise mode or paging. | `max_output_bytes=200_000` in `quant_research/tools.py`. | Context fills up, and later steps are crowded out. |
| B4 | **Overlapping tools.** `annualized_return` and `max_drawdown` are already contained in `analyze_performance`. | Four analytics definitions, 4.5 KB of JSON. | Each definition costs context on every turn and gives the model another choice to make. |
| B5 | **Only the tools primitive.** No resources (passages, runs, specs) and no prompts. Workflow guidance is one paragraph of server instructions. | `build_server` registers only `list_tools`/`call_tool`. | Clients re-derive the workflow on every task. |
| B6 | **Errors are flat strings.** A failure returns `"{status}: {error}"`. | `call_tool`. | The model has no structured hint for recovering. |
| B7 | **The evidence report is SDK-only.** The orchestrator plans with its own model, so MCP clients never receive the evidence-linked report. | `Platform.run`; B-12 open. | The product's main output is missing for the main audience. |

### C. Commercial readiness

| # | Gap | Why it matters |
| --- | --- | --- |
| C1 | **No tenant.** Runs carry an optional `principal`, but stores, corpora, and policy are global. | A hosted service must isolate customers' data, runs, and audit. |
| C2 | **No standard client auth.** API keys suit machines. Claude and Cursor connectors expect OAuth 2.1 as in the MCP authorization spec. | Connectors are how hosted MCP servers are adopted. |
| C3 | **No metering, quotas, or entitlements.** | Plans, fair use, and billing all need per-tenant usage. |
| C4 | **No compatibility promise.** Tool versions exist, but there is no deprecation policy, and skills are not tied to server versions. | Paying clients and published skills break silently. |

## Target design

```
  Claude / Cursor / Copilot / SDK agents
        │  skills (know-how, loaded on demand)        plugin or connector bundle
        │  MCP over Streamable HTTP (remote)  ─or─  stdio (local, open source)
        ▼
  ┌──────────────── apps/quant-platform (composition root) ────────────────┐
  │ auth: OAuth 2.1 resource server + API keys → Principal(tenant, subject,│
  │       scopes)                                                          │
  │ toolsets: research · analytics · factory   (entitlements per tenant)   │
  │ resources: passages, runs, strategy specs, datasets, templates         │
  │ prompts: one per skill workflow (fallback for clients without skills)  │
  │ metering hook · rate limits · OpenTelemetry                            │
  └───────────────────────────────┬────────────────────────────────────────┘
                                  ▼
       Core ToolExecutor (validation, permissions, approvals, limits) +
       RunRecorder + tenant-scoped stores (PostgreSQL with row-level security)
```

**Principles.**
1. **MCP is the capability boundary; skills are guidance.** Skills contain no scripts that do
   the work. Every action goes through the executor, so permissions, approvals, limits, and
   audit always apply.
2. **No server-side agents.** A workflow the platform owns is a deterministic composite tool
   (for example `research_report`). The orchestrator stays an SDK feature (decision 0001).
   Client-side subagents and A2A are deferred.
3. **Data by reference.** Tools take dataset ids or resource URIs. Inline arrays are accepted
   only below a small cap. The numbers never travel through the model.
4. **Remote first, local always.** The same server runs over Streamable HTTP (hosted or team)
   and stdio (local, open source). Contract tests assert that HTTP, stdio, REST, and direct
   calls agree.
5. **Writes to the user's machine stay local.** The remote server computes, retrieves, and
   validates. Anything that writes the user's files, such as `create_app`, runs through the
   local CLI from a spec the server checked, or returns an archive. Server-side code execution
   needs a sandbox and is out of scope.
6. **The tenant is the unit of isolation.** Each tenant has its own corpus, datasets, runs,
   audit, policy, and entitlements. Read-only by default holds per tenant.
7. **Ingestion stays an operator action.** For a tenant, the operator is the tenant admin, who
   ingests through an authenticated admin API or CLI. Ingestion is never an MCP tool.
8. **Efficiency is measured.** An offline agent-efficiency evaluation tracks definition size,
   calls per task, bytes per task, and success. Every phase reports its delta.

**Skill set** (instruction-only, versioned with the server, each declaring a minimum server
version):

| Skill | Teaches | Tools and resources it names |
| --- | --- | --- |
| `quant-research` | Searching the corpus, verifying passages, citing by chunk id, treating retrieved text as untrusted, the "not advice" framing | `find_related_signals`, `get_source_passage`, passage resources |
| `quant-analytics` | Uploading or naming a dataset, metric conventions (annualisation, risk-free, drawdown), reading computed evidence, never recomputing numbers in prose | `analyze_performance`, `analyze_risk`, dataset resources |
| `quant-strategy` | From signal to cited `StrategySpec` to report; surfacing open questions | `generate_strategy`, `research_report` |
| `quant-app-factory` | `describe_template` → `check_spec` → local `quant-factory create`; secrets as `env:` references | `describe_template`, `check_spec`, `plan_app` |

## Phases

Each phase is one PR, and each step is one commit. Phases P1 to P3 need no security change and
no new dependency. P4 to P7 change security defaults or deployment, so each starts with a
decision record and needs the user's approval before work begins.

### P1. Tool surface hygiene and an efficiency baseline (B-30)

1. **P1.1 Efficiency evaluation.** `tests/evaluation/agent_efficiency.py` uses a scripted fake
   client to run a fixed task suite (search and cite, analyse a series, draft a strategy, check
   a spec). It records definition bytes, calls, bytes per task, and success, then saves a
   baseline.
2. **P1.2 Output schemas.** `tool_definitions` adds `outputSchema` and `title`. The contract
   test checks that `structuredContent` validates against it.
3. **P1.3 Structured errors.** Failures return `structuredContent` with `status`, `error`, and
   a `hint` (for example the valid range, or the tool to call first), in place of a flat
   string.
4. **P1.4 Smaller default results.** Search gets `limit`, `cursor`, and a `detail`
   (`concise` | `full`) argument. Concise is the default and returns chunk ids plus short
   snippets. The default `max_output_bytes` is lowered where the evaluation shows no loss.
5. **P1.5 Fewer tools.** `annualized_return` and `max_drawdown` leave the MCP surface. They
   stay in the SDK and in `metrics.py`.
6. **P1.6 Mark untrusted content.** Retrieved passages carry `"trust": "untrusted"`, and
   descriptions say so. `security.md` documents that "no feedback into planning" binds the
   platform, not the client.

**Done when** the evaluation shows lower definition bytes and lower bytes per task at equal
success, and all contract tests pass.

**Status: done (2026-11-02).** Measured with the evaluation, before P1.2 and after P1.6:

| Metric | Before | After | Change |
| --- | --- | --- | --- |
| Tools served (research, analytics, factory) | 10 | 8 | −2 |
| Definition bytes the model sees per turn | 10,905 | 8,812 | −19% |
| Instruction bytes | 943 | 1,030 | +87 (untrusted-text rule) |
| Result bytes, five tasks | 24,100 | 23,611 | −2% |
| Argument bytes, five tasks | 9,696 | 9,696 | 0 (P2 moves series to datasets) |
| Calls, five tasks / success | 7 / 100% | 7 / 100% | — |

The fixture corpus has short chunks, so concise search saves little here (6,155 to 5,644 bytes
for search and cite); on real papers each passage preview shrinks by up to 400 characters.
Deviations from the steps above:
- **No cursor paging.** `k` (at most 25) already bounds a search, and paging a fused ranking
  would rerun every channel per page. Revisit when a client needs more than 25 passages.
- **Titles are derived** from tool names, so `ToolSpec` gained no field.
- **Output limits are unchanged.** `max_output_bytes` is a ceiling that fails a call, not a
  target, so lowering it would turn large `full` results into errors.
- **REST got the failure hints too**, because the branch now sits on the REST interface.

### P2. One composed server, resources, prompts, and data by reference (B-31)

1. **P2.1 Composition root.** `apps/quant-platform` (it replaces the superseded
   `apps/quant-ai-assistant`) wires the research, analytics, and factory registries into one
   server with `--toolsets research,analytics,factory`. Core still imports no product, and the
   products still do not import each other.
2. **P2.2 Resources and prompts in core.** `ResourceSpec` and `PromptSpec` are registered
   explicitly beside `ToolSpec`, and the MCP adapter maps them. The first resources are
   `qaos://passage/{chunk_id}`, `qaos://run/{run_id}`, `qaos://strategy/{id}`, and
   `qaos://template/{name}`.
3. **P2.3 Datasets.** A `DatasetRef` input type (a dataset id or a resource URI) and a dataset
   store with fingerprints. `analyze_performance` and `analyze_risk` accept `dataset` or a
   capped inline array. Upload goes through REST and the CLI
   (`quant-agent-os dataset push returns.csv`), never through the model.
4. **P2.4 Composite report.** `research_report` is deterministic. It takes a signal plus an
   optional dataset, and returns the evidence-linked report: retrieved evidence, computed
   evidence, and the strategy spec. No model is involved.
5. **P2.5 Prompts.** One MCP prompt per skill workflow, for clients without skills.

**Done when** a single server lists all toolsets, and analytics on a 2,500-point dataset costs
the client a constant number of input tokens.

**Status: done (2026-11-02).** Measured over the one platform server, against P1's three:

| Metric | P1 | P2 |
| --- | --- | --- |
| Servers a client adds | 3 | 1 |
| Tools / definition bytes per turn | 8 / 8,812 | 10 / 10,785 |
| Instruction bytes | 1,030 | 1,287 |
| Argument bytes, the five shared tasks | 9,696 | 542 (−94%) |
| Calls, the five shared tasks | 7 | 9 (two `list_datasets` lookups) |
| Result bytes, the five shared tasks | 23,611 | 24,785 |
| `research_report` (new task): calls / argument bytes | — | 1 / 89 |

Deviations from the steps above:
- **No inline cap.** Inline arrays stay accepted at any length: programmatic SDK and REST
  callers pay nothing for them, and the instructions, prompts, and (P3) skills steer models to
  references instead.
- **Run records are not resources yet.** A run store holds every caller's arguments, so run
  resources wait for tenancy (P5).
- **Dataset upload is a CLI command only.** An HTTP upload route is a new write scope, so it
  waits for P4's decision record.
- **Next to trim:** `plan_app`'s input schema (the full `AppSpec`, 2.9 KB) is now the largest
  definition.

### P3. Skills and client packaging (B-32)

1. **P3.1 Skills.** `skills/` holds the four skills in the table above, in the Agent Skills
   format (`SKILL.md` with name and description), with no executable scripts.
2. **P3.2 Consistency tests.** Every tool, resource, or prompt a skill names exists in the
   composed registry. Every served tool is covered by a skill. A skill's minimum server
   version is no newer than the current one.
3. **P3.3 Plugin bundle.** A Claude Code plugin with `.mcp.json` (stdio now, the remote URL
   after P4) and `skills/`. Equivalent config and rules snippets for Cursor and Copilot.
4. **P3.4 Docs.** `getting-started.md` is rewritten around "install the plugin", or "add the
   connector URL" after P4. AGENTS.md gains the principles above as invariants.

**Done when** the efficiency evaluation, run with the skills loaded as context, matches or
beats P2 on calls per task.

**Status: done (2026-11-02).** Measured with the evaluation, against P2:

| Metric | P2 | P3 |
| --- | --- | --- |
| Calls, the six tasks | 10 | 10 |
| Success | 100% | 100% |
| Definition / argument / result bytes | 10,785 / 631 / 28,901 | unchanged |
| Skill descriptions, every turn | — | 1,114 bytes (four skills) |
| One skill loaded per task | — | 2,415 to 2,642 bytes |

The evaluation's client is scripted, so the skills cannot change its calls. It checks instead
that every task calls only tools its skill teaches.

Deviations from the steps above:
- **The repository root is the plugin.** `.claude-plugin/plugin.json` declares the server
  inline, not in a `.mcp.json`, so the repository's own Claude Code setup is unchanged.
  `.claude-plugin/marketplace.json` lists it with `source: "./"`, so
  `claude plugin marketplace add yuxuzi/quant-agent-os` installs it.
- **The server finds its store.** `quant-platform` reads `QUANT_AGENT_OS_STORE` or
  `~/.quant-agent-os/knowledge.db`, and the default toolsets start without research when
  there is no store. A plugin cannot ask for a path, so it has to start anyway.
- **Python 3.13 is pinned in the plugin's command,** because bottleneck has no 3.15 wheels
  (decision 0010).
- **Cursor and VS Code bundles are generated** from the skills and the plugin
  (`client_bundles.py`), and a test fails while they are stale.
- **Skills name what they use in backticks.** The consistency test reads those names; there
  is no separate list to keep in step.

### P4. Remote MCP transport (B-33; decision 0012; needs approval)

1. **P4.1 Streamable HTTP.** `serve-mcp --http` uses the SDK's stateless Streamable HTTP
   manager on the same app as REST (`/mcp` beside `/v1`). DNS-rebinding protection checks
   `Origin` and `Host`. It binds `127.0.0.1` by default, and TLS terminates at the proxy.
2. **P4.2 Auth, first step.** The bearer-key verifier reuses decision 0011's keys and scopes,
   and the principal is recorded on every call. The server refuses to start without keys.
3. **P4.3 Contract.** HTTP equals stdio, REST, and direct calls. Tests cover missing and
   invalid tokens and wrong scopes, and check that no token appears in logs or errors.
4. **P4.4 Deployment.** `deploy/compose*.yaml` and `smoke-test.sh` exercise MCP over HTTP.
   Progress notifications are added for long tools.

**Done when** Claude Code connects with `claude mcp add --transport http` to the Docker team
server and passes the efficiency suite.

**Status: done (2026-11-02).** [Decision 0012](../../decisions/0012-remote-mcp-over-http.md)
accepted with the factory toolset as an optional extra. Deviations from the steps above:
- **No progress notifications.** Stateless JSON responses carry no stream to report progress
  on; long calls (`check_app`) are not served over HTTP. Revisit with P6.
- **The image serves research and analytics.** App Factory needs local writes (principle 5)
  and is left out.

### P5. Identity and tenancy (B-34; decision 0013; needs approval)

1. **P5.1 Principal and tenant.** `Principal(tenant, subject, scopes)` flows through the
   executor, recorder, and audit. Run-store schema 4 adds a tenant column.
2. **P5.2 Tenant-scoped stores.** The knowledge, dataset, and run stores are keyed by tenant,
   with PostgreSQL row-level security as a second line of defence. SQLite stays
   single-tenant.
3. **P5.3 OAuth 2.1 resource server.** Protected Resource Metadata, audience validation, and no
   token passthrough. Tokens are verified with an external identity provider (introspection,
   or JWT if a library is approved). Scopes map to toolsets. A test compose ships with a local
   identity provider.
4. **P5.4 Tenant admin.** An authenticated admin API and CLI for ingestion, datasets, keys, and
   policy. None of it is exposed over MCP.
5. **P5.5 Isolation tests.** Cross-tenant reads of passages, runs, datasets, and resources all
   fail, including through guessed resource URIs.

**Done when** two tenants on one server cannot observe each other in any test, and Claude
connects through OAuth.

**Status: done (2026-11-02).** [Decision 0013](../../decisions/0013-identity-and-tenancy.md)
was accepted with introspection, a schema per tenant, and the HTTP admin API left for later.
Deviations from the steps above:
- **The default tenant is `local`**, which the run store already recorded, so no migration
  was needed.
- **Tenant admin is operator commands.** `quant-platform tenant`, `ingest --schema`, and
  `dataset push --tenant` (Q-9).
- **The Keycloak test compose** is documented, not shipped, because Docker was not available
  to verify it.

### P6. Approvals in the client, and the remote/local split (B-35; decision 0014; needs approval)

1. **P6.1 URL-mode elicitation.** A consequential call returns `approval_required` and sends
   the human to the server's approval page through URL-mode elicitation. The approval is bound
   to the run, tool, and input hash, and used once, as in decision 0005. It is out of band, so
   neither the model nor the client can approve.
2. **P6.2 Form-mode fallback.** Only for local stdio sessions, where the operator is the
   user, and only if the operator enables it.
3. **P6.3 Factory over MCP.** `create_app` returns a checked spec plus either a
   `quant-factory create` command to run locally or a generated archive resource. It never
   writes files on the server.

**Done when** a client completes plan, approve, and apply without leaving the client, and an
approval cannot be forged by tool output (injection test).

**Status: done (2026-11-02).** [Decision 0014](../../decisions/0014-approvals-in-the-client.md)
was accepted with approver keys and four eyes except for the local tenant. Deviations from the
steps above:
- **Both ways in are opt-in.** The approval page needs `--approval-url`, and the in-client
  confirmation needs `--confirm-in-client` on `quant-factory serve-mcp` only (the platform's
  stdio server does not serve `create_app`).
- **SDK resume.** `Platform.resume` cannot yet resume a call with a secret-named argument;
  see the decision's notes.

### P7. Commercial readiness (B-36; [decision 0015](../../decisions/0015-metering-quotas-and-entitlements.md), accepted)

1. **P7.1 Metering.** A usage event per call (tenant, tool, duration, bytes, status), written
   with the run, plus a usage export.
2. **P7.2 Quotas and entitlements.** Rate limits and monthly quotas per tenant and plan. A
   toolset or knowledge pack outside the plan is not listed. Tenants get a clear error on
   quota and are never cut off mid-call.
3. **P7.3 Compatibility.** Tool versions follow semver, with a deprecation window and a
   changelog for tools. The server advertises its version, and skills check it. Done:
   `scripts/tool-versions.lock`, `qaos://server/info`, [tools changelog](../../tools-changelog.md).
4. **P7.4 Operations.** SLO dashboards from OpenTelemetry, and a security review of the remote
   surface (OWASP and MCP security best practices) before any public endpoint, folded into
   B-22. Done: telemetry and SLOs in [deployment](../../deployment.md), and the
   [security review](../../security-review.md), whose findings F-1 to F-5 are open.

Billing-provider integration, the licence for the hosted parts, and listings in MCP
registries wait for the open questions below.

## Deferred

- **Client-side subagents** (for example a literature reviewer with read-only tools). Only if
  the efficiency evaluation shows the skill alone fills the main context.
- **A2A and server-side agents.** The platform is a set of tools, not an agent (decision 0001).
- **Server-side code execution** (`generate_factor_code`, B-20, run remotely). Needs a sandbox
  design first.

## Open questions

- **Q-1. The open-core line.** Proposal: open source covers core, the products, stdio and
  single-tenant HTTP, and the skills. The hosted multi-tenant service, curated knowledge packs,
  SSO and audit export, and premium factory packs are paid. Is self-hosted multi-tenant open
  or paid?
- **Q-2. Identity provider for P5.** Introspection needs no new dependency. Local JWT
  verification needs a library (for example PyJWT).
- **Q-3. Datasets in the hosted service.** Retention, size limits, and whether licensed market
  data can be offered as datasets.
- **Q-4. Names.** Names are provisional, so registry listings and connector names wait for
  B-22's name checks.
