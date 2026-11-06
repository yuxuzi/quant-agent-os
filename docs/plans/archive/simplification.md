# Quant-Agent-OS: critique and simplification plan

## Context

The repo was reviewed as a quant firm would meet it, and then as an engineer maintaining it. It
was built quickly and carefully: 0.1.1 has about 16,000 lines of source, about 770 tests and 15
decision records. The core ideas are good. The problem is that it grew a vendor's hosted-SaaS
machinery before it had a smooth single-user path, and one number it computes can be silently
wrong.

**Status: complete; archived 2026-11-06.** P0 is released in 0.1.2. P1 and P2 are released in 0.2.0: see decisions 0016, 0017, and
0018; the `research` and `hosted` extras were left out because they would be empty.
P3 is complete and unreleased. Parquet (P3.3) was approved on 2026-11-05 and uses pyarrow, because
pandas 3.1 deprecates its fastparquet engine, and fastparquet failed on a pyarrow-written frame with
an unnamed date index and has no Python 3.15 wheel. P4, P5, and P6 are complete and unreleased.

**Compatibility.** The 90-day deprecation rule from decision 0015 is suspended until the project is
mature enough, so tools may change or be removed with a changelog entry.

**Decisions made for this plan (2026-11-04):**
1. **Customer:** a firm or team self-hosting. Tenants, plans, metering, and OAuth move behind an
   opt-in `hosted` extra. The code is kept but leaves the default path. This narrows the earlier
   "remote-first, sold as hosted" direction.
2. **Packaging:** one wheel with extras, installed from a git tag. Not PyPI (standing decision).
3. **App Factory:** stays in this repo, off by default on the server, CLI first.

**What to keep:** one executor behind every interface, evidence labels, read-only by default,
data by reference, instruction-only skills checked against the served catalog, known-answer
tests, and the real-paper evaluation.

**How the evidence was gathered:** figures marked *measured* came from read-only probes on
2026-11-04 using the repo's virtualenv and a scratch directory, including the 21 cached arXiv
papers. Nothing in the repo changed.

### The five findings that matter most

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | **Wrong numbers with no warning.** `periods_per_year` defaults to 12, and daily data gets monthly annualisation. | *Measured*, same 504 daily returns: Sharpe **0.195** by default against **0.895** at 252, and no warning |
| 2 | **Misaligned series compute happily.** `20200102`-style dates, common in CRSP-style files, are read as a numeric data column, so the alignment check is skipped. | *Measured*: benchmark identical but offset one row. Status `ok`, beta **-0.045**, true beta 1.0, no warning |
| 3 | **Real data files are refused outright.** One blank or `NaN` cell rejects the whole CSV. CSV is the only format. | *Measured*: both cases raise `DatasetError` |
| 4 | **"Graph RAG" is mostly keyword search on real papers.** | *Measured* on 21 papers: 357 edges, **338 `related_to`** (95%), 19 typed; 36 entities. Of 113 top passages over the gold questions, 11 had any graph channel and 6 were graph-only |
| 5 | **No clean install.** The plugin installs the server, but adding data still needs a clone. Installing wheels needs `--find-links` to a folder of downloads. | `docs/getting-started.md:36`, `docs/releasing.md:25` |

---

## Critique from the end user's side (quant firm or professional)

### U1. Unnecessary complexity, to remove

| Finding | Evidence | Action |
| --- | --- | --- |
| Vendor features in a firm's path: tenants, plans, quotas, metering, OAuth, approval page | About 1,480 lines; `serve-http` alone has 9 options and mode-dependent rules; `docs/deployment.md` is 340 lines | P1.2 |
| An orchestrator that cannot run for real. The only provider is fake, and `model_provider` is `Literal["fake"]` | `config/settings.py:25`, `providers/`; the README's "How it fits together" still leads with it, and Lab 1 teaches it | P1.1 |
| Settings that do nothing: `model_provider`, `model_name`, `api_key`, `max_plan_steps` | `config/settings.py:25-29`; a user may set an API key expecting an effect | P1.1 |
| Overlapping tools for one question: `generate_strategy` and `research_report` both give a cited write-up on a signal | Two tools, two prompts, a skill, a resource | P1.4 |
| `plan_app` is the largest definition and duplicates `check_spec`, which already returns problems or the exact files | *Measured*: 9.5 KB of the 43.6 KB full payload | P1.3 |
| Skills tell the model to read `server_info` once per session, although the plugin ships server and skills together | `skills/*/SKILL.md` | P1.4 |

### U2. Inconsistent design and workflow, to streamline

| Finding | Evidence | Action |
| --- | --- | --- |
| **Four CLIs, split by repo structure and not by task.** Ingest with `quant-research`, add data with `quant-agent-os`, serve with `quant-platform`, generate with `quant-factory` | `[project.scripts]` in four pyprojects | P2.2 |
| **Five ways to serve,** while the docs say "one server" | `quant-research serve-mcp`, `serve-api`; `quant-factory serve-mcp`; `quant-platform serve-mcp`, `serve-http` | P2.2 |
| **The default store location disagrees.** `quant-research ingest` without `--store` writes to `./.quant-research/knowledge.db`; the platform looks in `~/.quant-agent-os/knowledge.db` and then serves without research tools | `bootstrap.py:24`, `quant_platform/cli.py:79` | P2.3 |
| **Three audit locations, and the platform cannot read its own.** Platform: `~/.quant-agent-os/runs.db`; research: beside the store; factory: `factory-runs.db`. `quant-platform` has no `runs` command | CLI modules; an audit that is hard to find fails its purpose | P2.3 |
| **Five approval paths:** SDK approve and resume, terminal prompt, `--confirm-in-client` (only on `quant-factory serve-mcp`), the web page, standing pre-approval | `docs/decisions/0014`; `docs/plans/archive/agent-surface.md:317` | P1.2, P2.2 |
| **Three environment prefixes:** `QUANT_AGENT_OS_`, `QUANT_RESEARCH_`, `QUANT_FACTORY_`, plus `.env` read from the working directory, which a client-launched server does not control | `config/settings.py:20`, `docs/configuration.md` | P2.3 |
| **One name, two things.** `quant-research` is the product, CLI, skill, and also an App Factory profile: `quant-factory new quant-research my-lab` builds something unrelated to the product | `domain/spec.py:26` | P5.2 |
| **Three ways to choose a template:** `kind`, `template` + `profile`, and 1.x aliases "kept for one release", which is now past | `domain/spec.py:39-52` | P5.2 |
| **The docs teach different paths.** The README leads with `quant-research serve-mcp`, getting-started with the plugin, and `docs/architecture.md:640` names a `quant-agent-os serve-mcp` command that does not exist | See U4 | P6 |
| **Vocabulary mismatch.** A firm's own signal names need Python: the CLI has no vocabulary option, and the platform server always opens the bundled vocabulary | `quant_platform/cli.py:160`, `bootstrap.py:85` | P4.2 |

### U3. Inconvenient steps, to simplify

**Today, from nothing to a cited answer with metrics:**
```bash
claude plugin marketplace add yuxuzi/quant-agent-os
claude plugin install quant-agent-os@quant-agent-os
git clone https://github.com/yuxuzi/quant-agent-os && cd quant-agent-os && uv sync  # still needed
uv run quant-research ingest ~/research --store ~/.quant-agent-os/knowledge.db      # must type this path
uv run quant-agent-os dataset push returns.csv --id returns                         # a different CLI
# then restart the client; pass periods_per_year yourself or get monthly annualisation
```

**Target:**
```bash
uv tool install "quant-agent-os[research,analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@v0.2.0"
qaos ingest ~/research
qaos data add returns.csv
qaos connect claude        # or cursor, vscode; `qaos doctor` explains anything missing
```

| Finding | Action |
| --- | --- |
| Notebook use takes three imports and manual wiring; failures return silently as `record.output is None`; everything comes back as a dict; there is no pandas path, in a field that lives in DataFrames (`examples/quant-research/sdk_demo.py`) | P3 |
| Misaligned datasets are refused with "align them", when joining on dates and reporting dropped rows is what the user wants | P0.3 |
| No `doctor`: a missing store is only a warning on stderr, which clients hide, so research tools simply are not there | P2.2 |
| API keys never expire, and issuing or revoking one needs a restart (security review F-4), which is routine for a team server | P4.2 |
| Local embedders do not exist; the only real ones send text to Voyage or OpenAI, which a buy-side firm often cannot allow for proprietary research | P4.3 |

### U4. Documentation that misleads

- `docs/architecture.md` labels built things as "Proposed" (lines 23, 25, section 7's heading),
  says pytemplate-uv "is invoked as a CLI" (line 210, contradicting decision 0006), lists a
  `generate_report` tool that no code defines (line 660), and says hosted multi-tenant is out of
  scope (lines 749, 773) although it is built. Section 7 has a broken table.
- `docs/ENGINEERING_REVIEW_PLAN.md` keeps a day-by-day roadmap, an impossible `uvx` acceptance
  criterion (line 131), and "nothing below is built before day 90" for things already built.
- There are four onboarding routes (README, getting-started, walkthrough, a 1,067-line tutorial).
  About 6,500 lines of docs sit beside about 16,000 of source.

---

## Critique from the engineer's side

### E1. Designs that can be simpler

| Design | Problem | Action |
| --- | --- | --- |
| **Five distributions in lockstep** with `==` pins | Decision 0003 promises independent releases, and `docs/releasing.md` requires bumping all five together. The cost is paid with no benefit, and GitHub-only releases make it worse | P2.1 |
| **`PlanStep` in the executor's signature** | The executor's main job is one call, yet it depends on a planning model | P1.1 |
| **Composition root imports from CLIs:** `quant_research.cli.MCP_INSTRUCTIONS`, `quant_app_factory.cli.served_tools` | Interface modules are used as libraries (`composition.py:29,115`), so serving pulls in CLI code | P2.2 |
| **Two versions of each analytics tool:** by value (`tools.py`) and by reference (`referenced.py`), with parallel input models, plus `annualized_return` and `max_drawdown` kept for examples | One tool with `list[float] \| ColumnRef` is enough | P0.5 |
| **Parallel store adapters** (SQLite 614 lines, PostgreSQL 664) | Same schema and queries maintained twice; measure the overlap and extract what is shared, with no ORM | P4.4 |
| **FTS5-missing fallback** in the SQLite store | A second ranking path for an uncommon build; fail with a clear message instead | P4.4 |
| **`HashingEmbedder` reachable from production config** (`config.py:57`) | A test mock offered as a real choice | P4.3 |
| **Maintenance scripts in test folders;** a 3,229-line `tool-versions.lock` at the repo root | `tool_lock.py` and `client_bundles.py` are tools, not tests | P4.5 |

### E2. Designs behind current practice

| Design | Better practice | Action |
| --- | --- | --- |
| A new one-thread pool per call; timed-out threads pile up (F-2) | One bounded pool per executor gives backpressure and a clear "busy" status; heavy tools go through the killable runner | P4.1 |
| Planner parses free-text JSON from `generate(prompt)` | Model vendors offer native tool use, and this project's own direction leaves orchestration to clients. Remove it instead of modernising it | P1.1 |
| Dataset re-parsed from CSV into Python lists on every call | Cache by fingerprint; support Parquet (open question O-10) | P0.4, P3.3 |
| Financial inputs not typed for what can go wrong: no frequency, no calendar, no unit check | Infer and verify, refuse when unsure, and put the answer in provenance | P0 |
| Secrets-capable `.env` chosen by working directory | One config file in one home, with environment overrides | P2.3 |
| Hosted embedders hand-write HTTP, retries, and backoff on the standard library (354 lines) | Use `httpx` in an extra; it is already locked through `mcp` | P4.4 |
| CI and classifiers claim Python 3.15, while bottleneck has no wheels and the plugin pins 3.13; fresh worktrees pick 3.15 and fail | Add `.python-version` at 3.13; state the limit honestly | P2.1 |
| Quality claims lack an ablation test: nothing measures what the graph adds on real papers | Compare with and without each channel | P4.1 |

---

## The plan

Delivered the usual way: **one commit per step, one PR per phase**, with CI reported per OS and
Python. Every phase changing the tool surface reports its agent-efficiency delta. Schema changes
bump the tool version and update the lock. Numbers below marked "target" are estimates to check.

| Outcome | Now | Target |
| --- | --- | --- |
| Wheels / console commands | 5 / 4 | 1 / 1 (`qaos`; the name is provisional) |
| Ways to start a server | 5 | 1 (`qaos serve [--http]`) |
| Audit databases / env prefixes | 3 / 3 | 1 / 1 |
| Commands from nothing to a first answer | 5, plus a clone | 4, no clone |
| Tool definitions the model sees each turn | 11,008 bytes | Target about 7,500 by default |
| Silent wrong metrics from the probes | 3 cases | 0, pinned by known-answer tests |

### P0. Correct numbers first (independent, small; consider releasing as 0.1.2)

Do this first: it is a live correctness problem, and it does not depend on anything else.

1. **Frequency.** Remove the default of 12 from `periods_per_year` in `referenced.py:64,75` and
   `quant_platform/report.py:31`. Infer it from the dataset's dates. Without dates, require the
   argument. When a supplied value conflicts with the dates, warn. Report the value and how it
   was chosen in the output.
2. **Dates.** In `data/datasets.py`, treat a first column as the index when its name is `date`,
   `datetime`, `time`, or `period`, or when its values parse as `YYYYMMDD`. Add `--index` to set
   it explicitly.
3. **Alignment.** Join series on the index, and warn with the number of rows dropped. Refuse when
   series from different datasets lack dates and so cannot be aligned safely.
4. **Missing values.** Accept blank and `NaN` cells and keep them as missing. Per series, drop
   them with a warning and a count; for risk, use the common sample. Never forward-fill. Cache
   parsed datasets by fingerprint.
5. **One analytics tool per metric.** Merge by-value and by-reference, and drop
   `annualized_return` and `max_drawdown` as tools (the functions stay in `metrics.py`).
6. **Unit check.** Warn when returns look like percentages (for example, typical size above 0.2).
7. **Tests.** Turn these probes into known-answer tests. Update `skills/quant-analytics` and the
   `analyze_returns` prompt.

### P1. Remove and fence off

Starts with a decision record for each change: superseding 0001 (orchestrator) and amending
0012 to 0015 (hosted fence). **Save this critique as `docs/plans/simplification.md` and add
backlog items B-37 onward** as the first commit.

1. **Remove the orchestrator from the product.** Delete `Orchestrator`, `Platform.run`, the
   fake provider, and the unused settings. Replace `PlanStep` with a plain `ToolCall` in the
   executor. Check what still uses `reporting/generator.py` and keep only that. Move a short
   "drive these tools with your own model" example to `examples/`. Rework the injection tests
   against tools and the server. Close B-12, B-15, B-16, and B-20 as "not until a design partner
   asks", which also ends the "blocked on provider choice" state.
2. **Fence off hosted.** Move `tenants.py`, plan and quota implementations, usage export, OAuth,
   and the approval desk and page behind a `hosted` command group (an extra cannot exclude modules; see decision 0017).
   Core keeps small optional hooks (`UsageGate`) and the `tenant` column (default `local`), which
   is cheap and avoids a migration. The default HTTP server becomes one tenant with API keys.
   Move the hosted material from `docs/deployment.md` to `docs/hosted.md`.
3. **App Factory opt-in.** The default toolsets become research and analytics. Remove `plan_app`
   from the served set (the CLI keeps `plan`). Remove `--confirm-in-client`, since creating is a
   terminal action. `test_skills.py` then compares skills with the full catalog.
4. **One write-up tool.** Merge `generate_strategy` into `research_report(focus, returns?,
   benchmark?)`, with its resource and prompt, and keep the CLI command. Drop the `server_info`
   instruction from skills. Keep the tool and resource for operators.

**Approval gate:** moving the approval page and removing `--confirm-in-client` change security
surfaces. Neither widens permissions, but confirm each before merging.

### P2. One install, one command, one home

1. **One wheel.** Keep the five import names unchanged, so the boundary test still applies. Move
   sources under one root, and declare extras: `research`, `pdf`, `postgres`, `analytics`,
   `factory`, `server`, `hosted`, `all`. Base stays pydantic, pydantic-settings, rich, and typer,
   so generated agent-tool projects stay light.
   - **Spike first:** confirm `uv_build` handles several modules and the template packs' odd file
     names, and that `uv tool install` from a git tag works. If `uv_build` cannot, stop and ask
     before changing build backend.
   - Update `release.yml`, `ci.yml`, the Dockerfile, the compose files, the plugin command, the
     client bundles, and `docs/releasing.md`. Add `.python-version`. Shrinking the 15-job CI
     matrix is proposed but needs approval. **Editing CI and release workflows needs approval.**
   - Decision record superseding the packaging part of 0003.
2. **One CLI.** Each product exposes a Typer sub-app; `quant_platform` mounts them:
   `qaos ingest | search | strategy | data | serve [--http] | runs | keys | connect | doctor |
   init | app ... | hosted ...`. Remove the four old commands in the same release, as this is 0.x.
   Move served tools and instructions out of the `cli` modules. `connect` prints or writes a
   client's config, and only when the user runs it. `init --sample` loads the sample corpus.
   `doctor` checks home, store, datasets, extras, and permissions.
3. **One home.** `QUANT_AGENT_OS_HOME` (default `~/.quant-agent-os`) holds `config.toml`,
   `knowledge.db`, `datasets/`, `runs.db`, and `keys`. Every command uses it with no flags.
   Factory runs join the one run store. One environment prefix; embedding keys keep the standard
   `VOYAGE_API_KEY` and `OPENAI_API_KEY`. Read `.env` only from home. Add a one-time move of old
   locations, or a `doctor` message, whichever the spike shows is simpler.

### P3. Notebook path

1. `quant_platform.open_platform()` opens the home's store and datasets and returns a ready
   `Platform`.
2. `record.result()` returns the output or raises `ToolCallError`, carrying status and hint.
   `call` keeps its never-raises behaviour for interfaces.
3. `platform.data.put("fund", df)` registers a pandas object in an in-memory dataset store, so
   the data-by-reference path, dates, and alignment apply unchanged. Core only duck-types it.
   Parquet support comes through an extra (**new dependency: ask**).

### P4. Hardening and honest retrieval

**Done 2026-11-05; measured results are in [real-corpus-eval.md](../real-corpus-eval.md), C5 and C6.**
Owner's decisions while doing it: the local embedder ships as an opt-in extra; the `httpx` move in
step 4 is dropped, because `httpx` has no retry for HTTP errors and so would not remove that code;
and step 4's shared SQL turned out small (11 short statements and the store lifecycle).

1. **Ablation first.** Extend `real_corpus.py` to report hit rate and MRR with and without each
   channel, and report typed-fact counts. Use it to decide how the product is described.
2. **Firm vocabulary, and key handling.** `qaos ingest --vocabulary firm.toml` merges with the
   bundled one and is stored with the corpus, so serving matches what ingestion saw. Add key
   expiry and reload without a restart (F-4). Add the shared bounded pool and a `busy` status (F-2).
3. **Local embeddings.** An optional extra with a local ONNX embedder (**new dependency: ask**),
   default-off until it beats the real-paper baseline (paraphrase 0.833). Move `HashingEmbedder`
   to test support.
4. **Reduce duplication.** Extract SQL shared by the two stores; drop the FTS5 fallback; move
   embedders to `httpx` in their extra.
5. **Tidy.** Move maintenance scripts to `scripts/`, and the tool lock beside them.

### P5. App Factory cleanup

**Done 2026-11-06.** The owner approved the public `quant-baseline` mirror, now at v3.0.0. The
parity test found two silent errors, both fixed: the platform's Sharpe and information ratio divided by
floating-point noise on constant inputs, and generated `beta` did so against a constant market. Checking
the upgrade path also found that `pytemplate update` cannot reach a project built from a built-in pack.

1. **Metric parity test.** Generated `metrics.py` (numpy, deliberately portable under decision
   0009) must agree with `quant_analytics.metrics` on a fixed series, wherever conventions match.
   Document the rest. Note in `LIMITATIONS.md` that generated projects do not receive later fixes
   automatically.
2. **One way to choose.** Remove `kind` and the 1.x aliases. Rename the `quant-research` profile
   to `quant-lab`. **The public `quant-baseline` repo is mirrored by hand, so updating it needs
   approval.**

### P6. Documentation reset

**Done 2026-11-06.** Commands in the README and getting started are checked to exist, rather than being run:
running them needs a network and clients. The tutorial's own tests still run its labs.

- **One route:** README → getting-started (one page, the target commands) → tutorial. Fold the
  walkthrough's unique parts into these, then delete it. The tutorial is uncommitted work; see
  the coordination note below.
- **`architecture.md`:** rewrite as what exists today, dropping the status labels and staged
  tool table. Aim for about half the length.
- **`ENGINEERING_REVIEW_PLAN.md`:** reduce to open backlog and open questions. Move finished
  plans to `docs/plans/archive/`.
- **Product wording:** describe research as "cited keyword search with entity linking and a small
  graph of extracted facts", plus "optional semantic search" (owner's decision, 2026-11-05), and
  state what P4.1 measured. Update `AGENTS.md` invariants and the command table.

### Not in this plan

Each waits for a named design partner: `run_backtest`, market-data connectors, LLM extraction,
`.ipynb` or `.docx` loaders, SSO separate from the `hosted` extra, and any model provider.
PyPI stays out by the owner's decision. pytemplate-uv and the hub are not touched.

**Known risk:** a firm with Okta or similar will want OAuth without tenants. If the first partner
asks, split OAuth from `hosted` then.

---

## Verification

**Every phase:**
```bash
uv run pytest
uv run ruff check . --extend-exclude .claude
uv run ruff format --check . --extend-exclude .claude
uv run mypy
uv run python tests/evaluation/agent_efficiency.py        # report the delta; rebaseline only if intended
uv run python packages/quant-research/tests/research_eval.py
```

**By phase:**
- **P0:** known-answer tests reproduce each probe: daily frequency inferred (Sharpe 0.895);
  `YYYYMMDD` is an index; offset series align to beta 1.0 with a dropped-row warning; blank and
  `NaN` cells give a warning and correct metrics.
- **P1:** the import-boundary test passes; a default install with no `hosted` extra has no tenant
  or OAuth routes; the two-tenant isolation suite still passes with `hosted` installed.
- **P2, end to end on a clean environment:** with an empty temporary `HOME`, install from the
  branch's git ref; run `qaos init --sample`, `qaos data add`, then `qaos serve` through a real
  MCP client subprocess; ask a question and compute a metric; confirm the call shows in
  `qaos runs`. Run the Docker smoke test (`deploy/smoke-test.sh`, in CI since Docker isn't
  installed locally). Run `QUANT_FACTORY_SMOKE=1` for generated projects.
- **P3:** a notebook-style integration test with a DataFrame, from `open_platform()` to a raised
  `ToolCallError`.
- **P4:** PostgreSQL contract tests in CI, or locally through `pgserver`; `real_corpus.py eval`
  does not fall below `real_baseline.json`.
- **P6:** every command in the docs runs (`tests/integration/test_tutorial.py` extended to
  getting-started); link check.

## Gates

Each of these needs the owner's approval when reached:
- Removing the orchestrator (P1.1): **approved 2026-11-04**; see [decision 0016](../../decisions/0016-clients-orchestrate.md).
- Security-surface changes (P1.2, P1.3).
- CI and release workflow edits (P2.1).
- New dependencies: Parquet (P3.3), local embedder (P4.3), `httpx` as a direct dependency (P4.4).
- Updating the public `quant-baseline` mirror (P5.2).
- Any version bump, tag, or release, including 0.1.2 for P0.
