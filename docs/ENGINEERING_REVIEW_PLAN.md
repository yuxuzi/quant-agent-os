# Quant-Agent-OS engineering plan

> **Canonical plan** for Quant-Agent-OS: gap analysis, MVP, the 30- and 90-day roadmap, the
> backlog, and the decision log. The architecture it implements is in
> [architecture.md](architecture.md).
>
> **History.** This file used to be the QuantFoundry / uv-template engineering review. On
> 2026-10-30 it was rewritten around the Quant-Agent-OS product baseline. The earlier version,
> including the uv-template generator review and its phases, is preserved in git history (commit
> `797dac3`). `pytemplate-uv` 3.0 is published and is consumed here as an external tool. Its
> roadmap lives only in its own repository.

## 1. Direction

Quant-Agent-OS is an open-source quantitative intelligence and application-engineering platform
that AI clients consume through MCP, an SDK, and later REST. It is not a chatbot. It has two
products:
1. **Quant Research**: a Graph RAG knowledge system with source provenance.
2. **Quant App Factory**: generation of maintainable, tested Python applications.

Deterministic quantitative analytics are domain services registered as tools. `pytemplate-uv` is
a standalone scaffolder behind a replaceable adapter. See
[decision 0003](decisions/0003-brand-and-product-structure.md).

### Principles

The earlier plan's principles carry over, with two additions (9 and 10):
1. **Bring your own everything.** Users bring their models, keys, documents, data, and
   databases. The project supports a small documented set and does not claim universal
   compatibility.
2. **Lightweight core, explicit registration.** No plugin marketplace and no dynamic code
   loading.
3. **MCP is an adapter.** Logic lives in services; every interface goes through the same
   executor.
4. **One bounded orchestrator.** Specialist agents are added only after an evaluation shows
   value.
5. **Universal tool contract.** Every tool declares typed I/O, permissions, limits, and
   provenance.
6. **Evidence first.** `retrieved`, `computed`, and `interpretation` statements stay distinct,
   and retrieved text is untrusted.
7. **Deterministic finance.** The LLM does no arithmetic. Metrics have documented conventions
   and known-answer tests.
8. **Read-only by default.** Consequential permissions need a per-call approval
   ([0005](decisions/0005-approval-gates.md)).
9. **Platform, not chatbot.** Clients synthesize answers, and Quant-Agent-OS returns evidence
   and computed results.
10. **Smallest coherent change.** Preserve working code, and add infrastructure only for a
    named requirement.

## 2. Gap analysis (inspected 2026-10-30)

Baseline: `uv run pytest` gives 31 passed with 93% branch coverage; `ruff check`,
`ruff format --check`, and strict `mypy` (26 files) are all clean. CI covers Linux, macOS, and
Windows with Python 3.11–3.15. A release workflow uses PyPI Trusted Publishing.

### Existing capabilities (confirmed)

| Capability | Evidence |
| --- | --- |
| Typed tool contract, decorator, descriptors | `src/quant_agent_os/tools/contracts.py` |
| Explicit registry; read-only permission policy with allowlist | `tools/registry.py`, `tools/permissions.py` |
| Bounded executor with six recorded statuses | `runtime/executor.py`, `tests/unit/test_executor.py` |
| Single bounded orchestrator, no feedback into planning | `runtime/orchestrator.py`, decision 0001 |
| Evidence kinds and structured, rendered reports | `evidence/models.py`, `reporting/` |
| SDK-free MCP adapter, equal to direct calls | `mcp/adapters.py`, `tests/contract/test_tool_contract.py` |
| Settings with `SecretStr`, log redaction | `config/settings.py`, `observability/logging.py` |
| Offline end-to-end demo on synthetic data | `examples/portfolio-research/`, `tests/integration/` |
| Published standalone scaffolder with a JSON CLI and template packs | `pytemplate-uv` 3.0 (sibling repository) |

### Missing capabilities

| Gap | Needed for | Evidence |
| --- | --- | --- |
| MCP server transport | Claude demo | `mcp/adapters.py` docstring: "transport (milestone Q5)" |
| Any Quant Research code (ingestion, store, graph, retrieval) | Product A | No module; "Q4 … not started" in README |
| Any App Factory code (spec, planner, scaffold adapter, validator) | Product B | No module |
| Persistence: runs, audit, approvals | Provenance, approvals | Reports exist only in memory |
| Approval gate and subprocess runner | App Factory writes and execution | `Permission` exists; there is no approval concept; thread timeouts cannot kill |
| Provenance ids: run id, call id, input hash, timestamps, registry digest | Reproducibility | `ToolCallRecord` and `RunMetadata` fields |
| Determinism and idempotency on the contract | Baseline per-tool spec | `ToolSpec` fields |
| Real model-provider adapter | Server-side synthesis, spec drafting | Only `FakeModelProvider` |
| SDK facade, REST API | Interfaces | None |
| Analytics package | Real metrics | Metrics are examples in `examples/portfolio-research/portfolio_tools.py` |
| Evaluation suites | Citation accuracy, injection resistance | `tests/evaluation/README.md` only |

### Duplicated or stale

- Brand drift: "QuantAgentOS" appeared in the CLI help, docstrings, README, and CONTRIBUTING,
  and "QuantFoundry" in the README and `../quantfoundry.code-workspace`. The docs now follow
  decision 0003. Code-facing strings remain (B-04).
- `apps/quant-ai-assistant/` is a README-only placeholder for a chat app that the baseline
  replaces with external clients. It is now marked as superseded.
- The uv-template plan sections duplicated `pytemplate-uv`'s own plan. They have been removed
  here (see History).

### Coupling, risks, and technical debt

| Item | Severity | Evidence or mitigation |
| --- | --- | --- |
| A thread timeout cannot stop a running tool | High for `execute_code` | `ToolExecutor` docstring. Use a subprocess runner (B-09) |
| `runtime/__init__` cannot re-export the orchestrator because of an import cycle | Low | Documented in AGENTS.md. Keep as is |
| Example metrics live in `examples/` and are imported by the tests through `pythonpath` | Low | `pyproject.toml` `[tool.pytest]`. Move them to analytics (B-13) |
| Observability coverage is 68% | Low | Coverage report. Cover the redaction filter |
| Scope (two products for one developer) | High | MVP = one complete slice plus a factory foundation (section 3) |
| Copyright of ingested papers | Medium | Store only user-supplied documents. Hosted knowledge packs carry metadata, links, and licensed text only |
| Names unchecked | Medium | AD-5. Check before launch |

### Decisions already made

AD-1 to AD-5, AD-8, and decisions 0001–0002 (section 8).

### Decisions still open

Listed in section 10.

## 3. MVP: one complete slice plus the factory foundation

**User story.** A quant adds Quant-Agent-OS to Claude Desktop or Claude Code as an MCP server
and ingests a folder of research notes and papers. The quant then asks: "Which signals are
related to short-term reversal, and what supports that?" Claude calls `find_related_signals`
and `get_source_passage`, receives passages and graph paths with exact citations, and writes an
answer whose every claim links to a source. Claude can then call `analyze_performance` on
supplied returns, and `generate_report` produces an evidence-linked report. As a second
capability, the quant runs `quant-agent-os factory create` (or the `create_app` tool) with
approval to scaffold a new `agent-tool` package. The package passes its own checks and the
platform's contract tests.

### Acceptance criteria

1. `uvx --from 'quant-agent-os[mcp]' quant-agent-os serve-mcp` starts a stdio server. A local
   MCP client test lists the tools, and each call result equals the direct executor result.
2. `quant-research ingest packages/quant-research/tests/fixtures/corpus` builds a SQLite knowledge store
   offline. The rerun is idempotent, and the corpus snapshot id stays stable.
3. `find_related_signals` returns passages and graph paths. Every passage has a `SourceRef`
   with a location, and every graph edge cites a chunk.
4. The citation-accuracy evaluation on the fixture corpus records a baseline. Injection
   fixtures do not change tool selection or permissions.
5. `create_app` without an approval records `approval_required`. With an approval it scaffolds
   through `pytemplate-uv` 3.0, and the generated package passes `uv run pytest`, `ruff`, and
   `mypy` (an opt-in smoke test; the offline test uses a recorded fixture).
6. Every run is persisted with a run id, call ids, input hashes, the registry digest, and the
   corpus snapshot id.
7. Core imports neither product (an enforced test). The default suite runs offline with
   `FakeModelProvider`. Lint, format, and type checks pass.
8. A README walkthrough with a Claude Desktop configuration snippet reproduces the demo from a
   clean checkout with `uv sync --locked`.

**Status (2026-10-30).**
- **Met:** criteria 2, 3, 4, 5, 6, 7, and 8.
  - Criterion 5 is shown offline with recorded pytemplate-uv output, and live by the opt-in
    smoke test: real `agent-tool` and `python-cli` projects pass their own checks.
- **Criterion 1:** met through `quant-research serve-mcp`, run from a checkout. The `uvx` form
  waits for publishing, which needs a decision on names.

Out of the MVP:
- server-side synthesis;
- vector retrieval;
- PostgreSQL;
- REST;
- code generation beyond scaffolding;
- backtests.

## 4. Roadmap

The proposed day 1 is **2026-11-02**. The earlier dependency-ordered milestones (Q0–Q7) map
onto the days below, so there is still only one timeline.

| Window | Goal | Backlog | Earlier milestone |
| --- | --- | --- | --- |
| Days 1–7 | Foundation: decisions 0003–0005 accepted, workspace and import boundary, provenance fields, MCP stdio server | B-01–B-04 | Q0, Q5 |
| Days 8–14 | Quant Research slice: domain models, SQLite store, ingest CLI, fixture corpus | B-05 | Q4 (part) |
| Days 15–21 | Retrieval tools, citation and injection evaluations, SDK facade, run store | B-06–B-08 | Q4, Q6 |
| Days 22–30 | Approval gate, subprocess runner, App Factory foundation, demo and docs | B-09–B-11 | Q7 |
| Days 31–60 | Depth: real model adapter, analytics package, PostgreSQL + pgvector, `research_alpha`, AppSpec drafting, `generate_strategy`, `generate_report` over MCP | B-12–B-17 | Q1, Q3 |
| Days 61–90 | Hardening and release: REST + API-key auth, Docker team server, `generate_factor_code` with a bounded iteration loop, `analyze_risk`, security review, end-to-end tests, 0.2 release, name checks | B-18–B-22 | — |

## 5. Backlog (prioritized)

### First five tasks

**B-01. MCP stdio server** (days 1–7). **Done** on `feat/mcp-server`: `quant_agent_os.mcp.server` plus `quant-research serve-mcp`, verified end to end over a stdio subprocess.
- *Rationale:* this is the fastest route to a Claude demo using tools that already work.
- *Depends on:* approval to add the official `mcp` SDK as an optional extra.
- *Files:* `src/quant_agent_os/mcp/server.py`, `cli.py` (`serve-mcp`), `pyproject.toml` extra.
- *Acceptance:*
  - the server only forwards to `tool_definitions` and `call_tool`;
  - the CLI fails with an actionable message when the extra is missing.
- *Tests:*
  - an in-process client session test of list and call, equal to direct calls;
  - denied, invalid, and timeout cases.
- *Risk:* MCP SDK API drift. Pin a version range and keep the adapter SDK-free.

**B-02. Provenance and contract fields** (days 1–7)
- *Rationale:* the baseline's per-tool spec and reproducibility requirements.
- *Files:* `tools/contracts.py`, `runtime/executor.py`, `reporting/models.py`,
  `reporting/generator.py`.
- *Acceptance:*
  - adds `deterministic`, `idempotent`, `call_id`, `run_id`, `started_at`, `input_sha256`,
    `data_versions`, and `registry_digest`, all additive with defaults;
  - descriptors expose the new flags;
  - Markdown provenance shows the run id.
- *Tests:*
  - the same input gives the same hash;
  - the digest changes when a tool version changes;
  - existing tests are unchanged.
- *Risk:* report schema churn. Additive fields only.

**B-03. Workspace and import boundary** (days 1–7)
- *Rationale:* lets products land without coupling (decision 0003).
- *Files:*
  - root `pyproject.toml` `[tool.uv.workspace]`;
  - `packages/quant-research/` skeleton (`pyproject.toml`, `src/quant_research/__init__.py`,
    `tests/`);
  - `tests/contract/test_import_boundaries.py`.
- *Acceptance:*
  - `uv sync` installs the members;
  - the boundary test fails if `quant_agent_os` imports `quant_research` or
    `quant_app_factory`, or if the products import each other;
  - CI runs member tests.
- *Tests:* the boundary test, using AST scanning without a new dependency.
- *Risk:* workspace changes to CI. Ask before editing the workflow.

**B-04. Brand alignment in code-facing text** (days 1–7)
- *Rationale:* consistent positioning (decision 0003).
- *Files:* `cli.py` help, package docstrings, `pyproject.toml` description.
- *Acceptance:* the user-visible strings say Quant-Agent-OS; module and distribution names are
  unchanged.
- *Tests:* the CLI help test.
- *Risk:* none.

**B-05. Quant Research store and ingestion** (days 8–14). **Done** (commits `1f94aa5`–`b58acbd`).
- *Rationale:* the core of product A.
- *Files:*
  - `quant_research/domain/` (Document, Chunk, Entity, Relation, CorpusSnapshot, `KnowledgeStore`
    protocol);
  - `infrastructure/sqlite_store.py` and `memory_store.py`;
  - `application/ingest.py`;
  - `vocabulary/quant-v1.toml`;
  - the CLI `quant-research ingest`;
  - `tests/fixtures/corpus/` (about 10 synthetic, freely licensed notes).
- *Acceptance:*
  - ingestion is idempotent by content hash;
  - every chunk has a location and every relation cites a chunk;
  - the snapshot id is stable across reruns.
- *Tests:*
  - shared contract tests for both stores;
  - known-answer extraction tests on the fixtures;
  - an FTS5-missing fallback test.
- *Risk:* entity-resolution quality. Measure it, then improve the vocabulary.

### Remaining backlog

| ID | Task | Depends on | Acceptance (summary) |
| --- | --- | --- | --- |
| B-06 | **Done.** Hybrid retrieval (keyword + graph, RRF); `find_related_signals`, `get_source_passage` tools; citation and injection evaluations. Step plan: [plans/quant-research-retrieval.md](plans/quant-research-retrieval.md) | B-05 | Criteria 3–4 in section 3 |
| B-07 | **Done.** SDK `Platform` facade (`call`, `run`, `get_run`, `runs`) in `quant_agent_os.sdk` | B-02 | Same records as the MCP path |
| B-08 | **Done.** `RunStore` (memory, SQLite), `RunRecorder`, append-only audit events; `serve-mcp` records every call; `quant-research runs` | B-02 | Criterion 6; no secrets in the audit (test) |
| B-09 | **Done.** Approval gate, `approval_required` status, standing approvals, subprocess runner (decision 0005 accepted). Plan: [plans/approvals-and-app-factory.md](plans/approvals-and-app-factory.md) | B-08 | Approvals bound to input hashes and single use; hard kill on timeout |
| B-10 | **Done.** App Factory foundation: `AppSpec`, `ArchitecturePlanner`, `ScaffoldProvider`, `PytemplateUvScaffolder`, `agent-tool` pack, `plan_app`/`create_app`, `quant_agent_os.testing.check_tool`, CLI `quant-factory plan/create` | B-03, B-09 | Criterion 5; recorded JSON fixture; no import of `pytemplate-uv` |
| B-11 | **Done for Quant Research** ([walkthrough.md](walkthrough.md), verified from a clean clone with `uv sync --locked`). The App Factory part waits for B-10 | B-01, B-06, B-10 | Criterion 8 |
| B-12 | First real `ModelProvider` adapter (optional extra) and opt-in live evaluations | provider choice | Offline tests unchanged; secrets never logged |
| B-13 | Analytics package: move the example metrics; add Sharpe, volatility, beta, and tracking error with conventions and known-answer tests | — | Imports no LLM library; `analyze_performance` registered |
| B-14 | `PostgresKnowledgeStore` + pgvector, embedding provider port, vector channel gated by evaluation | B-06, B-12 | Passes the shared store contract tests |
| B-15 | `research_alpha`: server-side synthesis and hypotheses as `interpretation` evidence | B-06, B-12 | Every interpretation cites passages; injection evaluation passes |
| B-16 | AppSpec drafting from requirements (a model proposes, the human confirms) | B-10, B-12 | The draft validates against `AppSpec` before any scaffold |
| B-17 | `generate_strategy` (a structured strategy specification from research evidence) | B-06 | The output is a spec, not code; the spec links its sources |
| B-18 | REST API (FastAPI extra) and API-key auth with scopes and a principal in audit events | B-07, B-08 | REST equals MCP results (contract test) |
| B-19 | Docker image and team-server deployment guide (PostgreSQL) | B-14, B-18 | `docker compose up` demo; no secrets in the image |
| B-20 | `generate_factor_code` and the bounded fix-and-validate loop (decision record first) | B-09, B-10, B-12 | At most N rounds; structured diagnostics; approval for each write |
| B-21 | `analyze_risk` (exposures, volatility contributions) in analytics | B-13 | Known-answer tests |
| B-22 | 0.2 release, security review, name availability checks | all | Release gates in section 9 |

`run_backtest` is planned for after day 90 as its own package with explicit cost, slippage,
calendar, and no-look-ahead tests. Pull it forward only when design-partner demand shows a need.

## 6. Commercial path (proposed, to be validated)

The goal is open-core sustainability without compromising the open project. Nothing below is
built before day 90. The architecture only keeps the right seams open:
- tenancy and principal columns in the run store;
- audit events usable for metering;
- separate distributions so licenses can differ per package.

| Offer | Value | Architectural prerequisite |
| --- | --- | --- |
| Open-source Core + products (MIT) | Adoption, credibility, contributors | This plan |
| Team or enterprise server: SSO/OIDC, RBAC, audit retention and export, approval workflows, PostgreSQL deployment support | Firms need governance before agents touch research and code | B-08, B-09, B-18, B-19 |
| Curated knowledge packs: quant factor and signal graphs with metadata, links, and licensed summaries | Time saved on literature review | B-05, B-14; licensing review |
| Premium App Factory packs (factor libraries, data-pipeline services, research APIs) | Faster production-quality builds | B-10 |
| Hosted Quant-Agent-OS (later) | No operations burden | Multi-tenant work, currently out of scope |
| Support, design partnerships, custom integrations | Early revenue and product feedback | MVP demo (B-11) |

Validation steps:
- recruit 3–5 design partners (quant researchers or small funds) after the MVP demo;
- track weekly active MCP installations (opt-in only, no telemetry by default), stars, and
  partner retention;
- decide the license boundary (for example, enterprise features in a separately licensed
  package) before day 90 (open question O-4).

## 7. Quality gates per change

Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy`.
Products follow the same contract tests. Each user-visible change gets a CHANGELOG entry.

## 8. Architecture decisions

| ID | Decision | Status |
| --- | --- | --- |
| AD-1 | One bounded orchestrator; specialists only after an evaluation ([0001](decisions/0001-one-orchestrator-first.md)) | Decision |
| AD-2 | Logic lives in services and libraries; MCP, REST, and SDK are adapters over one executor ([0002](decisions/0002-mcp-boundary.md)) | Decision |
| AD-3 | Read-only by default | Decision |
| AD-4 | Bring your own everything, with a small documented support set | Decision |
| AD-5 | All names are provisional until availability and trademark checks | Decision |
| AD-6 | Superseded by AD-10 | Superseded |
| AD-7 | (pytemplate-uv render engine; now owned by that project) | Moved |
| AD-8 | `pytemplate-uv` is independent; Quant-Agent-OS invokes it only as a pinned CLI with JSON in and out, and owns its own template pack | Decision |
| AD-9 | Quant-Agent-OS is the brand and platform; two products; one repository as a uv workspace with Core at the root ([0003](decisions/0003-brand-and-product-structure.md)) | Proposed |
| AD-10 | A relational knowledge graph behind `KnowledgeStore`; SQLite for the MVP, PostgreSQL next, a graph database only on evidence ([0004](decisions/0004-knowledge-store.md)) | Proposed |
| AD-11 | Per-call approvals for consequential permissions; subprocess runner for `execute_code` ([0005](decisions/0005-approval-gates.md)) | Proposed |
| AD-12 | In the MVP, clients synthesize answers and Quant Research returns evidence packets | Proposed |
| AD-13 | Ingestion is an operator command, not an agent tool | Proposed |
| AD-14 | Stay on Python ≥ 3.11 (matches `pytemplate-uv` and CI); raise it only when a dependency requires 3.12 | Proposed |

## 9. Risks, release gates, and success metrics

Risks:
- **Two products for one developer.** Mitigation: the MVP has one complete slice; the second
  product is a foundation only.
- **Injection through documents.** Mitigation: read-only agents, no feedback into planning, and
  injection fixtures.
- **Weak extraction quality.** Mitigation: deterministic vocabulary first, measured with the
  citation evaluation.
- **Metric conventions.** Mitigation: known-answer tests.
- **MCP SDK and provider drift.** Mitigation: isolated adapters.
- **Copyright.** Mitigation: user-supplied corpora and licensed packs only.
- **Names.** Mitigation: AD-5.

Release gates for 0.2:
- all MVP acceptance criteria are met;
- no open critical or high findings;
- the evaluation suites pass with recorded baselines;
- a clean-checkout demo works.

Success metrics (to be confirmed after the first baseline):
- citation accuracy at or above the agreed threshold;
- injection resistance of 100% on the fixtures;
- 100% of known-answer metric tests pass;
- generated `agent-tool` packages pass their checks on the first run;
- the demo reproduces in under 10 minutes.

## 10. Open questions

- **O-1:** Which LLM provider gets the first adapter (B-12)?
- **O-2:** Which MCP SDK version range is supported, and when is a remote transport (streamable
  HTTP) needed?
- **O-3:** What is the final name of the analytics package (derived from `pyfolio-reloaded`)?
- **O-4:** Where is the license boundary for commercial features: MIT everywhere plus services,
  or a separately licensed enterprise package?
- **O-5:** Should a pinned, allowlisted command (`uvx pytemplate-uv==3.0.*`) get a narrower
  permission than `execute_code`, so that read-only `plan_app` needs no approval?
- **O-6:** What are the distribution names for the products (`quant-agent-os-research`,
  `quant-agent-os-factory`)?
- **O-7:** Does `pytemplate-uv` render packs from a subdirectory of a git repository, or must
  the pack ship as package data and be passed as a local path? This decides how the
  `agent-tool` pack is versioned.
- **O-8:** How is iteration in App Factory (B-20) reconciled with the no-feedback invariant?
  This needs its own decision record.
