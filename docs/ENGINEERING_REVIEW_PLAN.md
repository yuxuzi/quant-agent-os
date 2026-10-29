# Engineering Review and Evolution Plan: QuantFoundry Ecosystem and uv-template Application Factory

> **Location.** This plan moved from `pytemplate-uv` to `quant-agent-os/docs/` on 2026-10-28. In this
> plan, "QuantAgentOS" is implemented by this repository (`quant_agent_os`). Sections about
> uv-template describe the sibling `pytemplate-uv` project, whose Phase 3.1–3.3 work is tracked
> in its own [PLAN.md](https://github.com/yuxuzi/pytemplate-uv/blob/main/PLAN.md). Its Phase 2 record is archived in that repository's `docs/plans/`.

## Product direction

`uv-template` is an existing Python project generator. Its intended direction is to become an **AI-powered application factory** for creating well-structured quantitative-finance applications, while remaining useful for straightforward Python projects.

The product should make it easier to create reliable projects through a combination of curated project profiles, explicit configuration, and AI-assisted guidance or generation where that is genuinely valuable. The basic generator must remain understandable, deterministic, and lightweight; AI features should be optional rather than becoming a hidden dependency of every generated project.

`uv-template` is one component of the wider **QuantFoundry** ecosystem described below. Within that ecosystem it is the developer and application factory. It is a generic tool: it knows nothing about QuantAgentOS. QuantAgentOS calls it to scaffold tool packages, MCP server projects, and applications from a template pack that QuantAgentOS owns (AD-8). Its generation path stays deterministic and does not depend on QuantAgentOS or any AI service.

## QuantFoundry ecosystem and QuantAgentOS

> **Provisional names.** QuantFoundry, QuantAgentOS, Quant AI Assistant, Quant Search, and the portfolio analytics package name are working names. They have not been checked for availability and must not be treated as legally or commercially cleared.

QuantFoundry is the umbrella ecosystem. QuantAgentOS is its agent runtime and architecture. Quant AI Assistant is the flagship application built on QuantAgentOS. QuantAgentOS is an architectural evolution of this plan, not a separate project with its own roadmap.

### Component boundaries

| Component | Responsibility | Boundary | Depends on |
| --- | --- | --- | --- |
| QuantFoundry | Umbrella brand and ecosystem | Naming, documentation, release coordination | — |
| QuantAgentOS | Agent runtime, bounded orchestrator, tool registry, workflow execution, safety controls | Typed tool contract; provider and data-source interfaces | Registered tools only, never their internals |
| Quant AI Assistant | Flagship user-facing application | User interface and report presentation | QuantAgentOS |
| uv-template (`pytemplate-uv`) | Generic developer and application factory | Profiles, components, external template packs, generation manifest, JSON CLI | Nothing in the ecosystem; QuantAgentOS invokes it through `uvx` |
| Portfolio analytics (derived from `pyfolio-reloaded`; final name TBD) | Deterministic quantitative engine | Plain Python API, usable without an LLM | Numerical libraries only |
| Quant Search | Research retrieval and evidence engine | Retrieval API returning passages with source identifiers and locations | User-supplied documents and stores |
| MCP integrations | Interoperability boundary for supported tools and services | MCP servers that wrap library functions | The library they expose |

Layering rule: business logic lives in independently tested libraries (analytics, Quant Search). QuantAgentOS registers and orchestrates that logic, and applications compose QuantAgentOS. Dependencies point downward only. MCP adapters call the same library functions as direct Python callers and never reimplement them.

### Schedule

The ecosystem's 90-day build phase and the later adoption, promotion, and commercialization phase stay authoritative where they are defined. That schedule is **not** in this repository. The QuantFoundry MVP milestones below are ordered by dependency and carry no dates, so they can be mapped onto the 90-day plan without creating a second timeline (see [Unresolved questions](#unresolved-questions)).

## QuantAgentOS architectural principles

1. **Bring Your Own Everything (BYOE).** Users configure their own LLM providers and models, market-data providers, research documents and repositories, databases and vector stores, and compatible MCP servers. QuantAgentOS provides orchestration, typed interfaces, tool discovery, workflow execution, and safety controls. Support starts with a small, documented set of integrations; do not promise universal compatibility. Credentials come only from the environment or a supported secret manager and are never committed.
2. **Plugin-first, lightweight core.** Capabilities are added through explicit, typed interfaces and registered tools. The MVP uses simple Python adapters and MCP servers only. No plugin marketplace, no arbitrary dynamic code loading, and no elaborate plugin framework.
3. **MCP where it is a useful boundary.** Use MCP to standardize the boundary to external tools and services. Core logic stays in ordinary, independently testable Python functions; MCP adapters call those functions. Direct in-process calls remain available wherever they are simpler.
4. **One bounded orchestrator.** The initial platform has a single orchestrator that interprets a request, creates a constrained plan, selects registered tools, executes the workflow, validates tool outputs and evidence, produces a structured report, and records provenance and limitations. Specialist agents (Research, Coding, Backtest, Reporting) are possible later extensions, added only when evaluations show measurable value. No multi-agent swarm in the MVP.
5. **Universal tool contract.** Every tool declares its identity and description, typed input and output schemas, capabilities and permissions, validation and error behavior, timeouts and output-size bounds, and provenance and execution metadata. The first release has a small set of tools for analytics, research retrieval, and local documents, not every proposed connector.
6. **Data ownership and research evidence.** Users bring their own data and storage where supported; the platform does not require ownership of users' databases, vector stores, or research repositories. Quant Search preserves source identifiers and locations, supports evidence-linked answers, and separates retrieved facts from model-generated interpretation. Retrieved documents are untrusted input, and resistance to prompt injection is tested.
7. **Financial correctness and reproducibility.** The analytics package stays deterministic and usable without an LLM. Financial metrics have documented conventions, known-answer tests, regression tests, and explicit warnings for invalid or insufficient data. Reports preserve, where practical, inputs or input identifiers, analysis parameters, benchmark assumptions, tool and package versions, source references, warnings, and run metadata.
8. **Security boundaries.** The MVP is read-only by default. No autonomous trading, unrestricted code execution, or high-impact external actions in the initial release. Use least-privilege permissions, secure credential handling, input validation, explicit approval for consequential actions, and logging without secrets.

## QuantFoundry MVP: one end-to-end workflow

The MVP proves a single workflow:

1. The user supplies permitted portfolio data, a benchmark, and relevant research documents.
2. The orchestrator validates the request and creates a bounded plan.
3. Registered tools invoke deterministic analytics and research retrieval.
4. The system validates outputs and evidence.
5. The assistant generates a structured report with metrics, citations, assumptions, warnings, and provenance.

Constraints: achievable by one developer working incrementally; demonstrated on synthetic data; local documents without a mandatory vector database; one model-provider adapter plus a fake provider for tests. Broad connector coverage, mandatory vector databases, complex multi-agent orchestration, hosted multi-tenant infrastructure, and autonomous trading are deferred.

### QuantFoundry MVP milestones

Milestones are listed in dependency order. Each must be mapped onto the authoritative 90-day plan rather than scheduled independently.

#### Q0 — Architecture and component boundaries

- Scope: the component table and principles above, plus the [architecture decisions](#architecture-decisions).
- Acceptance criteria: every component has a named owning package and a documented dependency direction; no component depends upward on QuantAgentOS or an application.

#### Q1 — Deterministic analytics core

- Depends on: uv-template Phases A–B for a reliable package scaffold.
- Scope: a small set of core portfolio and benchmark-relative metrics in the analytics package.
- Acceptance criteria: each metric has a documented convention (returns frequency, annualization, risk-free rate) and known-answer tests; regression tests pin results on fixture data; invalid or insufficient data produce explicit warnings or errors; the package imports no LLM or agent library.

#### Q2 — Tool contract and registry

- Depends on: Q1.
- Scope: a minimal typed tool contract and in-process registry in QuantAgentOS; Q1 metrics registered as the first tools.
- Acceptance criteria: invalid inputs are rejected against the declared schema; timeout and output-size limits are enforced in tests; every invocation emits provenance and execution metadata; tools can be listed and described without being executed.

#### Q3 — BYOE configuration boundaries

- Depends on: Q2.
- Scope: model/provider and data-source interfaces with one real provider adapter and a fake provider.
- Acceptance criteria: configuration is read only from the environment or a secret manager; missing or placeholder credentials fail fast with an actionable error; a test asserts that secrets never appear in logs; the full test suite runs offline using the fake provider.

#### Q4 — Research retrieval with evidence

- Depends on: Q2.
- Scope: Quant Search over local documents, registered as a tool.
- Acceptance criteria: every returned passage carries a source identifier and location; a citation-accuracy evaluation runs on a fixture corpus with a recorded baseline; prompt-injection fixture documents do not alter the plan or tool selection; retrieved facts and model interpretation are labelled separately in outputs.

#### Q5 — MCP server for tested functions

- Depends on: Q2 (and Q4 for the retrieval tool).
- Scope: an MCP server exposing the Q1 analytics tools and the Q4 retrieval tool by calling the same library functions.
- Acceptance criteria: a local MCP client test returns results identical to direct Python calls; denied, invalid, and timed-out tool calls are tested; the server is read-only.

#### Q6 — Bounded orchestrator and structured report

- Depends on: Q2–Q4.
- Scope: the single orchestrator running the MVP workflow and producing a structured report.
- Acceptance criteria: the end-to-end workflow runs on synthetic data with the fake model; the report contains metrics, citations, assumptions, warnings, and run metadata (inputs or input identifiers, parameters, benchmark, package versions); plans never exceed the configured step limit or tool allowlist; unsupported or unsafe requests are refused with a stated reason.

#### Q7 — Demonstration and release gate

- Depends on: Q5–Q6.
- Scope: a synthetic-data demonstration of the full workflow and reproducible setup instructions.
- Acceptance criteria: setup from a clean checkout succeeds with `uv sync --locked`; security, regression, and evidence-evaluation suites pass in CI; the demo is documented and reproducible without real credentials or market data.

Mapping to existing milestones: Q1 builds on Milestone C, Q2/Q3/Q6 realize Milestone D, and Q5 realizes Milestone E. uv-template Phases A–B are prerequisites for scaffolding the QuantFoundry packages.

## Objectives

1. Inspect the current implementation and document its real behavior, limitations, and quality level.
2. Critically evaluate the existing design against the target of an AI-powered quantitative application factory.
3. Identify confirmed issues, design constraints, and risks in the current implementation.
4. Propose a phased, minimal plan to improve the generator without prematurely building every future capability.
5. Recommend the single highest-value next implementation task, then wait for approval before making implementation changes.

## Scope and constraints

This document guides the engineering review. During the review:

- Inspect files and run only safe, relevant existing commands.
- Do not modify, create, delete, or reformat project files other than this plan document.
- Do not install dependencies, make external changes, or run destructive commands without approval.
- Do not expose secrets if encountered.
- Base conclusions on inspected files and actual command output.
- Clearly distinguish confirmed defects, design preferences, and speculative risks.
- Do not implement recommendations until approval is given.

## Phase 1 — Establish the current baseline

### Repository and execution flow

- Explain the purpose of important files, packages, modules, templates, and configuration files.
- Identify public interfaces and entry points, including CLI commands.
- Trace the full path from a user command to a generated project.
- Explain how templates are stored, discovered, rendered, copied, and validated.

### Packaging and developer experience

- Review `pyproject.toml`, the build backend, dependencies, Python-version support, and `uv` usage.
- Evaluate alignment with current Python packaging practices.
- Identify unnecessary dependencies, questionable version constraints, and portability concerns.

### Tests and observable behavior

- Inventory existing tests and state precisely what each test verifies.
- Run relevant existing tests without installing dependencies; report actual results and any blockers.
- Evaluate coverage of success paths, invalid input, missing files, output-directory conflicts, and failure recovery.
- Do not state coverage percentages unless they are measured.

## Phase 2 — Critique the existing design

Evaluate the implementation critically rather than merely describing it.

- Assess separation of concerns, modularity, error handling, configuration, logging, observability, and maintainability.
- Assess whether profiles can be added without changing unrelated core logic.
- Identify brittle assumptions in paths, templates, user input, filesystem behavior, platform support, and subprocess execution.
- Identify security concerns relevant to generating files, rendering templates, and executing commands.
- Rank findings as critical, high, medium, or low, with file paths and evidence.
- Separate confirmed defects from preferences and risks that require further validation.

## Phase 3 — Evaluate the AI-powered quant-app target

Phase 3 turns the reliable CLI generator into a flexible application factory for quantitative services. It must **not** become one large template that installs every framework, cloud SDK, database driver, AI provider, and trading feature. The generator should compose a small, explicit set of reviewed capabilities into a project whose behavior remains understandable without an AI service.

### Phase 3 design rules

- Generated projects remain deterministic and reviewable. The same validated render request produces the same file layout and declared dependencies.
- A profile owns the application shape; components add narrowly defined capabilities. A minimal CLI or library must not inherit web, database, or AI dependencies.
- Every optional capability has a manifest, compatible-profile list, dependency group, template fragment, configuration schema, validation rules, tests, and documentation.
- Configuration distinguishes development-safe defaults from production-required values. Secrets are never generated as usable production credentials or committed into source control.
- No generated project may perform network calls, create remote resources, run migrations, or contact an AI provider merely because it was rendered.
- Database, identity, market-data, model-provider, and MCP implementations are replaceable adapters behind application interfaces.
- AI assistance proposes a validated render request. It does not bypass the profile contract, silently execute commands, or make unreviewed changes to generated projects.
- Stable profiles and the generator are tested on every supported CPython minor release from 3.11 through 3.14. Raising the minimum version remains an explicit compatibility decision.

### Reference-informed practices to adopt selectively

The `simple-modern-uv` project is a useful reference for a minimal, maintained `uv` project foundation. Its approach reinforces several goals for this factory, and since Phase 2 this project also renders with Copier (see AD-7). Its Copier-managed update lifecycle still does not transfer directly, because this project's profile and component contract and its bundled-package template source differ. Adopt the practices below deliberately rather than copying its implementation wholesale:

- **Selective capability adoption:** preserve a user's chosen build backend, layout, and profile choices; report what is added, adapted, preserved, and deferred before generating or upgrading a project.
- **Reproducible `uv` workflows:** commit and validate `uv.lock` for projects that choose `uv`; CI uses locked installation and detects stale lockfiles. Evaluate a dependency cool-off policy before making it a default, balancing supply-chain protection against timely security fixes.
- **Focused quality gates:** use Ruff for linting/formatting, pytest for tests, and evaluate a modern type-checker policy (current mypy versus BasedPyright) with generated-profile evidence before standardizing one.
- **Agent-readable project guidance:** generate concise `AGENTS.md` instructions containing only the selected profile's build, test, lint, type-check, and security conventions. It must be useful to people and agents and must never authorize unreviewed deployment or secret access.
- **Release and maintenance discipline:** provide trusted publishing, provenance, release documentation, changelogs, and an explicit upgrade/migration policy. Do not claim that generated projects can receive safe template updates until that capability has been designed and tested.

### Phases 3.1–3.3 — Generic factory contract, FastAPI foundation, and authentication

These phases are generic and belong to `pytemplate-uv`. They are planned and tracked in its
[PLAN.md](https://github.com/yuxuzi/pytemplate-uv/blob/main/PLAN.md) for the 3.0.0 PyPI release. Following AD-8, nothing in them depends on or names
QuantAgentOS. The work is summarised below.

- **3.1 Contract:** versioned profile and component manifests (`contract = 1`) and deterministic
  resolution (`requires`, `conflicts`, compatibility) with actionable errors. Also:
  - `plan`, `validate`, and `create` with `--json` and stable exit codes;
  - `schema` exports JSON Schemas;
  - manifest v2 with file ownership;
  - explicit external **template packs** (`--template DIR|gh:org/repo --template-ref TAG`) under the same
    `unsafe=False` rules;
  - a documented compatibility policy and a `pytemplate.api` Python surface.
- **3.2 FastAPI foundation:**
  - a layered service (api, application, domain, infrastructure, bootstrap) with health and
    readiness endpoints, structured logging with correlation IDs, and one error envelope;
  - optional `database` (SQLAlchemy 2.x, Alembic, repository/unit of work, SQLite by default,
    PostgreSQL group), `worker` (task-queue seam), and `container` components.
- **3.3 Authentication:** an optional `auth` component in `api-key` or `oidc` mode, with:
  - a principal-and-scopes boundary;
  - an audit sink, log redaction, and a rate-limit seam;
  - production checks that refuse placeholder secrets and permissive CORS.

Acceptance criteria that matter to this repository:
- `uvx pytemplate-uv==3.0.* plan --json` and `schema request` let QuantAgentOS validate a
  render request without importing `pytemplate-uv`.
- A pack in a separate repository renders through `--template` with no change to
  `pytemplate-uv`.

### Phase 3.1a — QuantAgentOS template pack (owned by quant-agent-os)

Generation of QuantAgentOS-specific projects lives here, built on the pytemplate-uv pack
interface rather than inside the generator.

Scope:
- Add a `templates/` pack to quant-agent-os (`pytemplate-pack.toml`, `contract = 1`). It holds an
  `agent-tool` profile: a package of `@tool` functions with pydantic input and output models,
  declared `Permission`s, and `warnings`/`evidence` outputs, plus an explicit registry builder.
- Add an `mcp-server` component in the same pack. It wraps the tool package with
  `quant_agent_os.mcp` and contains no business logic (decision 0002). It is gated on Phase 3.6's
  concrete workflow before it becomes `stable`.
- Add a `quant-agent-os new-tool NAME` command that writes a render request and runs
  `uvx pytemplate-uv==3.0.* create-from-config <request.toml> --json`, where the request sets
  `template` and `template_ref` to the pack. The CLI is the only
  coupling: there is no import or dependency on `pytemplate-uv` (ask before adding one).
- Later packs build on the same interface: `market-data`, `research`, `backtest` (3.4) and
  `ai-agent` (3.5).

Acceptance criteria:
- A generated `agent-tool` package validates against the QuantAgentOS tool contract: its
  contract test builds a `ToolRegistry`, checks every `descriptor()`, and compares an MCP call
  with a direct call.
- The pack renders offline from a local path and from a `vX.Y.Z` git tag.
- `pytemplate-uv` is not a dependency of `quant-agent-os`, and `quant-agent-os` is not a
  dependency of `pytemplate-uv`.

### Phase 3.4 — Introduce quantitative research and data foundations

Create a quantitative profile/component set focused on reproducible research and controlled data handling, not an all-in-one trading platform.

Scope:

- Start with one use case: market-data ingestion, normalized time-series storage, feature/indicator calculation, and reproducible analysis runs.
- Define ports for data vendors, market calendars, storage, and execution. Provide at most one example/offline adapter; vendor credentials and live-provider assumptions remain optional.
- Preserve provenance: dataset identifiers, source timestamps, timezone/currency conventions, adjustment assumptions, configuration, code revision, and result artifacts.
- Separate research computation from live trading. Backtesting should model costs, slippage, calendars, and look-ahead-bias safeguards explicitly, but execution/brokerage connectivity is deferred.
- Offer a notebook-friendly research option only if it does not become required runtime infrastructure; the service/CLI path remains first class.
- Expose analytics as plain, deterministic Python functions so they can be registered as QuantAgentOS tools without modification, following principle 7 (financial correctness and reproducibility).

Acceptance criteria:

- A generated quant project completes a sample offline data-to-analysis workflow with deterministic test fixtures.
- Data adapters can be replaced without changing analysis/domain code.
- Tests demonstrate explicit handling of timezone, missing data, and a basic no-look-ahead invariant.
- No live trading account, market-data credential, or external network access is required to generate or test the project.

### Phase 3.5 — Add opt-in AI assistance and an agent application capability

Treat AI as a bounded product feature, not the generator's control plane.

Scope:

- Add an optional advisor mode that turns a user's natural-language goal into a proposed profile/component configuration, explains trade-offs, and requires explicit user confirmation before rendering.
- Validate AI output against the same typed render-request model used by the CLI; retain a serializable proposed plan for review and reproducibility.
- Define an agent capability for generated applications with provider-neutral interfaces for models, tools, prompts, memory/state, and policy checks. Generated agent applications target the QuantAgentOS tool contract and its single bounded orchestrator rather than defining their own. Initial scope should be one provider adapter and one bounded workflow.
- Require explicit configuration for model provider (BYOE), model credentials, model name, data sources, network access, tool allowlists, data retention expectations, cost limits, and human approval for consequential operations.
- Add evaluation fixtures for generated suggestions and agent workflows; keep deterministic unit tests independent of provider calls.

Acceptance criteria:

- Manual generation remains fully functional with no AI dependency or account.
- Advisor output can be inspected, edited, validated, and rendered only after confirmation.
- Agent tests use fakes/recorded responses by default and cover rejected unsafe tool requests.
- Provider-specific code is isolated to adapters and can be omitted from projects that do not select the capability.

### Phase 3.6 — Optional MCP integration for generated projects

MCP is an integration boundary, not a default dependency of every AI or quant project. Within QuantFoundry itself, MCP is the supported interoperability boundary and is built in milestone Q5. For projects generated by `uv-template`, it stays an optional component evaluated against a concrete workflow.

Scope:

- Select one concrete use case, such as exposing a read-only research catalog to an agent or connecting a generated agent service to a bounded internal tool.
- Choose whether the generated application is an MCP client, an MCP server, or neither; do not generate both without a stated workflow.
- Implement explicit tool schemas, least-privilege permissions, server allowlists, request/response validation, timeouts, audit logging, and failure handling.
- Keep MCP transport and SDK dependencies in an optional component; offer local mocked integration tests.

Acceptance criteria:

- Disabling MCP removes its dependencies and leaves the generated project fully functional.
- The selected MCP workflow has end-to-end tests against a local fake server and tests for denied/invalid tool use.
- Documentation states data exposure, permissions, trust boundaries, and operational failure modes.

### Phase 3.7 — Controlled template updates and migrations

Implemented in 2.1: generated projects carry `.copier-answers.yml` pointing at the `vX.Y.Z` release tag. `pytemplate update` runs `copier update` as a dry run by default, requires a clean git tree, and marks conflicts inline. The scope items below remain the design constraints. Do not overwrite an instantiated project to make it resemble a newer template. Build an update path only after the generated-project manifest can identify ownership and detect user changes.

Scope:

- Standardize generated project workflows on `uv` commands and remove Makefiles; `make` is not a supported cross-platform requirement.
- Extend `.pytemplate.toml` with the profile version, generator version, sanitized render context, and a file-ownership inventory containing hashes of generator-owned files. Never record secrets or environment values.
- Start with versioned, explicit migrations that are additive or narrowly targeted. Each migration declares its source and target profile versions, preconditions, changed paths, rollback guidance, and tests.
- Implement `pytemplate plan-update` before any write command. It renders/computes the proposed migration, classifies files as unchanged, user-modified, added, removed, or conflicted, and emits a reviewable plan or patch.
- Implement `pytemplate apply-update` only after the plan is approved. It must never overwrite a user-modified file, must write a backup/transaction record, and must stop on conflicts.
- Evaluate a future three-way merge strategy using the original rendered baseline, the new profile version, and the user project. Copier now renders the templates (AD-7). Evaluate `copier update` as the three-way merge mechanism only once templates are published from a versioned git source and the manifest records file ownership. Projects generated before the migration need a documented path and must never be silently rewritten.

Acceptance criteria:

- A generated project has a platform-neutral documented workflow using `uv`; it does not require `make`, shell activation, or Docker for local tests.
- A dry-run update produces a deterministic, human-readable plan and performs no writes.
- User-modified files are detected by hash and remain untouched unless the user resolves the conflict explicitly.
- Every supported migration has fixture projects at its source and target versions, including conflict, rollback, and idempotency tests.
- Projects generated before update support receive a documented migration path or are explicitly reported as unsupported; they are never silently rewritten.

### Phase 3.8 — Release-readiness and adoption loop

Validate that the factory can be safely used as a professional PyPI package before expanding the template catalog.

Scope:

- Publish profile/component compatibility documentation, versioning and deprecation policy, changelog, security reporting guidance, and contributor instructions.
- Add build, wheel-install, generated-project, lint/type-check, and cross-platform CI coverage appropriate to each supported capability. CI must cover CPython 3.11–3.14 while those versions remain supported.
- For profiles that opt into `uv`, generate and commit `uv.lock`; use `uv sync --locked` (or an equivalent lock-consistency check) in local documentation and CI. Evaluate, document, and test any dependency cool-off policy before enabling it.
- Generate profile-aware `AGENTS.md` guidance and test that it names only commands and security boundaries actually provided by the selected profile.
- Decide and document a template evolution strategy. Initially this may be a one-way generation contract plus migration guides; adopting Copier or another update mechanism requires a separate compatibility and safety design.
- Add a release workflow using PyPI Trusted Publishing, provenance where available, and pre-release testing on TestPyPI; do not store a long-lived PyPI token in repository secrets.
- Collect feedback from one or two real quant-app creation workflows before standardizing further profiles.

Acceptance criteria:

- A clean-environment installation from the built wheel can list, inspect, and generate every advertised stable profile/component.
- CI reports supported-platform results for the generator and generated examples.
- A `uv`-enabled generated project can be recreated from its committed lockfile in CI, and stale locks fail visibly.
- Generated agent guidance is concise, profile-aware, and contains no secrets, deployment authority, or unbounded command permission.
- A documented release checklist and rollback/deprecation policy exist.
- New capability requests are evaluated against user evidence, maintenance cost, security implications, and composability before being added.

### Phase 3 explicit deferrals

- Autonomous agents that write arbitrary source code, run shell commands, trade, or deploy infrastructure without a review/approval boundary.
- Live trading, broker execution, portfolio custody, payment flows, or regulatory/compliance claims.
- A built-in user/password identity system, multi-tenant entitlement product, or bespoke authorization server.
- Mandatory Docker, Kubernetes, cloud accounts, external databases, live market-data providers, or AI credentials.
- Multiple model providers, multiple task queues, or many market-data vendors before one adapter is validated in production-like use.
- MCP integration before a specific user workflow proves that direct application interfaces are insufficient.
- A plugin marketplace, arbitrary dynamic code loading, or an elaborate plugin framework.
- Multi-agent swarms, or specialist agents added before evaluations show measurable value over the single orchestrator.
- Claims of universal provider or connector compatibility, broad connector coverage, or mandatory vector databases.
- Hosted multi-tenant infrastructure.

## Phase 4 — Produce the engineering review

Deliver a prioritized report containing:

1. Executive summary and current maturity assessment.
2. Architecture and execution-flow explanation.
3. Strengths worth preserving.
4. Findings ranked by severity, with evidence and affected file paths.
5. Test results, test gaps, and limitations of the test run.
6. Recommended target architecture for deterministic core generation, profiles, and optional AI capabilities.
7. Risks, trade-offs, and features to defer.
8. The single highest-value next task.

## Phase 5 — Proposed implementation roadmap

After the review, propose milestones with scope and acceptance criteria. The roadmap should remain minimal and revise priorities based on Phase 1–4 evidence.

### Milestone A: Stabilize the current generator

- Address confirmed correctness, safety, packaging, and test deficiencies.
- Define reliable input, output, and error-handling behavior.
- Acceptance criteria: core generation paths work predictably; errors are actionable; high-value regression tests cover the supported behavior.

### Milestone B: Establish an extensible profile system

- Introduce a clear profile contract and profile discovery mechanism.
- Keep the minimal profile independent of optional domains.
- Acceptance criteria: a new profile can be added through the defined extension boundary without modifying unrelated core logic.
- QuantFoundry link: the QuantAgentOS tool contract (Q2) reuses the same typed-validation approach as the profile contract.

### Milestone C: Add quantitative-finance foundations

- Add one deliberately scoped quant profile, based on actual user workflows.
- Include only essential structure, configuration, tests, and documentation.
- Acceptance criteria: the generated project is runnable, tested, and useful as a quant-app starting point without unnecessary services or dependencies.
- QuantFoundry link: the deterministic analytics core is milestone Q1.

### Milestone D: Add optional AI assistance

- Design explicit AI-provider and AI-feature interfaces.
- Add bounded, opt-in assistance for one valuable workflow, such as project configuration or scaffold selection.
- Acceptance criteria: non-AI generation remains fully functional; AI behavior is transparent, configurable, testable, and safe by default.
- QuantFoundry link: the tool registry, BYOE configuration, and bounded orchestrator are milestones Q2, Q3, and Q6.

### Milestone E: Evaluate optional MCP integration

- Define a narrow MCP use case only if it provides user value beyond direct library integrations.
- Keep MCP adapters isolated from the generator core and profile contract.
- Acceptance criteria: MCP can be enabled or omitted independently, with documented permissions, failure modes, and tests.
- QuantFoundry link: the QuantFoundry MCP server for tested analytics and retrieval functions is milestone Q5.

## Explicit deferrals

Unless the review supplies compelling evidence, defer:

- A general autonomous coding agent that writes arbitrary project code.
- Supporting multiple AI providers at initial launch.
- Multiple large quant domains or a complete trading platform template.
- Mandatory cloud accounts, credentials, databases, or network services.
- Complex plugin ecosystems before profile boundaries and the QuantAgentOS tool contract are validated.
- MCP integration in generated projects before a concrete, well-tested workflow requires it.
- Multi-agent orchestration in QuantAgentOS before evaluations show measurable value.
- Autonomous trading, unrestricted code execution, and high-impact external actions.
- Hosted multi-tenant infrastructure, mandatory vector databases, and broad connector coverage.

## Architecture decisions

| ID | Decision | Rationale | Status |
| --- | --- | --- | --- |
| AD-1 | QuantAgentOS starts with one bounded orchestrator, not a multi-agent system. | A single constrained plan is easier to test, audit, and reproduce. It has fewer failure and prompt-injection surfaces than agents passing work between each other, it is feasible for one developer, and the MVP workflow does not need parallel specialists. Specialist agents are added only when evaluations show measurable gains. | Decision |
| AD-2 | Core logic lives in plain Python libraries; MCP is an adapter. | Keeps logic testable without a transport, avoids duplicate implementations, and keeps direct calls available. | Decision |
| AD-3 | The MVP is read-only by default. | Limits the impact of model errors and injected instructions while evidence and approval controls mature. | Decision |
| AD-4 | BYOE with a small documented support set. | Users keep ownership of models, data, and storage without the project promising universal compatibility. | Decision |
| AD-5 | All ecosystem names are provisional. | No availability or trademark checks have been done. | Decision |
| AD-6 | Local documents without a mandatory vector database for Quant Search in the MVP. | Reduces infrastructure while still testing citation and evidence behavior. A vector store becomes a BYOE adapter later. | Recommendation |
| AD-7 | Copier replaces Cookiecutter as the uv-template render engine; Typer remains the CLI. `copier update` is supported from the `vX.Y.Z` git tags of this repository (see Phase 3.7). | Validated questions, verbatim copy of non-`.jinja` files, and a future update path. Templates run with `unsafe=False` and declare no tasks. Details are in the pytemplate-uv [Phase 2 record](https://github.com/yuxuzi/pytemplate-uv/blob/main/docs/plans/phase-2.md). | Decision |
| AD-8 | The dependency between uv-template and QuantAgentOS runs one way. `pytemplate-uv` is a generic factory with no QuantAgentOS code, dependency, or naming. QuantAgentOS invokes it as an external CLI (`uvx pytemplate-uv==3.0.*`, JSON in and out) and supplies its own profiles as a template pack. | Each package can be released and tested alone. The generator stays useful outside QuantFoundry, and contract-specific templates evolve with the contract they target. See Phases 3.1–3.3 and 3.1a. | Decision |

## Risks, release gates, and success metrics

### Risks

- **Scope across six components for one developer.** Mitigation: the strict Q0–Q7 dependency order and the deferral lists above.
- **Prompt injection through retrieved documents.** Mitigation: treat retrieved text as untrusted, keep the MVP read-only, and maintain injection fixtures (Q4).
- **Incorrect financial-metric conventions.** Mitigation: documented conventions, known-answer and regression tests (Q1).
- **Provider API drift.** Mitigation: provider code isolated in adapters, and a fake provider for all default tests (Q3).
- **Naming conflicts.** Mitigation: availability checks before any public release (AD-5).

### Release gates

- All Q7 acceptance criteria are met.
- No open critical or high findings in the QuantFoundry components.
- Security, regression, and evidence-evaluation suites pass in CI.

### Success metrics (recommendations, to be confirmed)

- Citation accuracy on the fixture corpus meets an agreed threshold, set after the first baseline run.
- All known-answer metric tests pass, with no unexplained regression-fixture changes.
- The end-to-end synthetic-data demo is reproducible from a clean environment.

## Unresolved questions

- **Open:** Where is the authoritative 90-day build plan and the post-launch adoption, promotion, and commercialization plan? Milestones Q0–Q7 need to be mapped onto it without creating a second timeline.
- **Open:** Does a project workbook or tracker exist elsewhere that must be kept consistent with this document?
- **Open:** What is the final name of the analytics package derived from `pyfolio-reloaded`?
- **Open:** Which LLM provider gets the first BYOE adapter?
- **Open:** Which MCP SDK version and transport will the Q5 server support?

## Phase 2 review findings — current CLI generator

> **Superseded.** The Phase 2 critique, decisions, and step checklist are archived in the pytemplate-uv [Phase 2 record](https://github.com/yuxuzi/pytemplate-uv/blob/main/docs/plans/phase-2.md). The findings below record the original Phase 1 baseline; most were fixed before the Copier migration and are kept for history.

The following findings are grounded in the Phase 1 inspection and test output. They describe the implementation at that time, not the desired design.

### Critical

1. **The test suite is not runnable.** `pytest` recursively collects the unrendered tests under `templates/`, where Cookiecutter expressions are invalid Python syntax. The root test module also imports a nonexistent `cli` module. The recorded run collected zero tests and stopped with four collection errors. Evidence: `pyproject.toml` has no `testpaths`; `tests/test_cli.py` imports `from cli import app`; template tests contain `{{cookiecutter.package_name}}` imports.

2. **Generated FastAPI projects are inconsistent at runtime.** The settings model declares a required `SECRET_KEY`, and a generated `.env` supplies a placeholder. More importantly, application configuration defines `DATABASE_URL`, but `db.py` ignores it and uses a separate hard-coded `sqlite+aiosqlite:///./test.db` URL. Test fixtures create an in-memory engine but do not override the application's `async_session`; endpoint tests can therefore use the hard-coded database instead of the fixture database. Evidence: `templates/fastapi-template/{{cookiecutter.project_name}}/{{cookiecutter.package_name}}/core/config.py`, `db.py`, and `tests/conftest.py`.

### High

1. **The documented and tested CLI contract does not match the implementation.** The actual application exposes one root Typer command with positional `project_name`; the README and root tests expect a `create-project` subcommand and `--name` or `--project-name` options. The documented executable is also `pytemplate-uv`, while package metadata installs `pytemplate` and `create-project`. Evidence: `README.md`, `tests/test_cli.py`, `pytemplate/main.py`, and `pyproject.toml`.

2. **Template availability after package installation is not established.** Runtime template discovery walks from the source module to a sibling top-level `templates/` directory. The package-data declaration attempts to include paths using `../templates`, while the only declared package is `pytemplate`. This makes wheel and installed-package behavior uncertain and must be verified before relying on a published CLI. Evidence: `pytemplate/main.py` and `pyproject.toml`.

3. **The generator mixes orchestration, discovery, configuration, presentation, and process execution in one module.** This impedes focused tests and leaves no stable contract for profiles, validation, output locations, or non-interactive use. Evidence: all generator responsibilities reside in `pytemplate/main.py`.

### Medium

1. **The generator shell-out model provides weak failure diagnostics.** `subprocess.run()` does not capture standard output or error, so the subsequent diagnostic branches normally have no content. It also does not handle a missing `cookiecutter` executable or other `OSError` failures. Evidence: `pytemplate/main.py`.

2. **Input and output behavior are underspecified.** Project names are passed straight to Cookiecutter; there is no explicit project-slug/package-name validation, output-directory option, dry-run, preflight conflict summary, or structured result. `--force` delegates overwrite behavior directly to Cookiecutter. This is not a confirmed exploit, but it is a reliability and safety risk for a project generator. Evidence: `pytemplate/main.py`.

3. **Template metadata is not a profile contract.** The two templates are discovered only by directory naming. Their `cookiecutter.json` files contain overlapping, unstructured keys and inconsistent Cookiecutter directives, so capabilities, supported options, compatibility, and validation cannot be queried by the CLI. Evidence: both template `cookiecutter.json` files.

4. **The generated projects contain broad and mismatched defaults.** Both profiles include substantial tooling and configuration that the core generator does not use. The FastAPI profile includes database, CORS, security, and pre-commit defaults, with permissive host/CORS defaults and dependencies such as `pre-commit` in runtime dependencies. This is a design concern, not a claim that every default is inherently wrong. Evidence: generated `pyproject.toml`, `.env`, and FastAPI core modules.

### Low

1. **Metadata and release hygiene drift.** `setuptools_scm` is configured although the project version is static, generated `version.py` reports a different development version, and the declared changelog URL points to a file absent from the inspected repository. Evidence: `pyproject.toml`, `version.py`, and repository file inventory.

## Improvement plan: evolve from a template wrapper to a reliable CLI generator

### Phase A — Restore a trustworthy baseline

Scope:

- Define one public CLI contract and make the executable name, command hierarchy, arguments, documentation, and tests agree.
- Configure test discovery to exclude unrendered template source, repair root test imports, and add hermetic tests using temporary output directories.
- Resolve template packaging and add an installed-artifact verification test.
- Remove generator-runtime dependencies that belong only to generated profiles or development tooling.

Acceptance criteria:

- `pytest` completes collection and runs the root generator tests.
- `--help`, documentation, and tests describe the same CLI syntax.
- An installed wheel can list and render its bundled profiles without a source checkout.

### Phase B — Separate the CLI generator core

Scope:

- Split the current module into CLI presentation, profile discovery, request validation, rendering service, and result/error reporting.
- Use Cookiecutter as a library behind a renderer adapter, or retain a subprocess adapter with captured output and explicit executable errors.
- Add `--output-dir`, `--dry-run`, a conflict preflight, and explicit overwrite semantics.
- Validate names and normalize the package identifier according to a documented policy.

Acceptance criteria:

- Core rendering can be tested without Typer or terminal output.
- Invalid requests and filesystem conflicts produce actionable, stable errors.
- A render request yields a structured result containing profile, output path, and affected files.

### Phase C — Introduce versioned profile contracts

Scope:

- Define profile metadata: identifier, display name, version, description, template location, required inputs, optional components, dependency groups, and compatibility constraints.
- Provide `list-profiles` and `describe-profile` commands.
- Keep the minimal Python CLI/library profile independent, then migrate FastAPI as a separately owned profile.
- Prefer declarative profile options over scattered Cookiecutter keys.

Acceptance criteria:

- Adding a profile uses its own manifest and template directory without edits to renderer core logic.
- The CLI validates profile-specific input before files are rendered.
- Profile metadata is independently unit-tested.

### Phase D — Repair and scope the generated profiles

Scope:

- Make generated packaging, scripts, Makefiles, READMEs, tests, and dependency groups internally consistent.
- For FastAPI, use settings as the single source of truth for database configuration; inject database sessions in tests; remove placeholder production secrets from tracked runtime configuration; and make security defaults deliberate.
- Add generation smoke tests for each profile, then run the generated project tests in an isolated temporary environment when dependencies are available.

Acceptance criteria:

- Each generated profile passes its own focused smoke test.
- The FastAPI profile can start and test against a configured isolated database.
- Development tools are not installed as runtime dependencies unless they are required at runtime.

### Phase E — Prepare optional domain and AI extensions

Scope:

- Add one narrowly defined quant profile only after the core profile contract is stable.
- Define optional components such as data access, research notebooks, backtesting, or service APIs as explicit profile capabilities.
- Design AI assistance as an opt-in planning/configuration layer that produces a validated render request; it must not bypass validation, silently run commands, or be required for deterministic generation.
- Evaluate MCP only for a concrete, bounded integration after the CLI and profile boundaries are established.

Acceptance criteria:

- Non-AI generation remains complete and deterministic.
- An AI-assisted request is reviewable before rendering and maps to the same validated request model as manual CLI input.
- Optional integrations can be absent without affecting baseline profiles.
