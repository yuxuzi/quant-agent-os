# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `quant-factory` records runs in `factory-runs.db` beside `factory.toml`, so
  `QUANT_FACTORY_SETTINGS` moves both.

- Serving `find_related_signals` with a hosted embedder now needs `network` both granted and
  pre-approved (`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`), because each `network` call needs
  an approval.
- Python 3.15 is supported and tested in CI (3.11–3.15 on Linux, macOS, and Windows).

### Fixed

- Quant baseline studies now use the backtest's trade timing (B-27 R1). A study scored a
  signal against the return starting at the signal's own close, while the backtest trades one
  bar later, so a signal with a one-bar edge looked strong in the study (rank IC 0.27) and
  earned nothing in the backtest. Studies, IC decay, and quantile spreads now measure returns
  from the trade, using `config.TRADE_LAG`; results record the lag, and both CLIs take
  `--lag`. Regenerate existing projects, or re-run studies, to get comparable numbers.
- Quant Research on real papers (measured on 21 arXiv papers, `docs/plans/real-corpus-eval.md`):
  - Searches for things the corpus never mentions now return no passages instead of
    passages that share a few common words.
  - Typed relations are no longer read from tables, reference lists, run-on sentences, or
    entity types a predicate cannot join. Labelled precision rose from 0.30 to 0.75.
  - PDF text has ligatures expanded and line-break hyphenation undone, so terms such as
    "momentum" match across line breaks.
  - Stores re-process on the next ingest, because the extractor (`rules-v5`), chunker
    (`page-v2`), and PDF loader (`pdf-v2`) versions changed.
- Serving and searching open the knowledge store read-only, so they never migrate or write it,
  and a read-only mount works. Answers read one consistent corpus state. Snapshots are built
  and published atomically. The schema migration is atomic and can be re-run.
- Hosted query embedding has a deadline inside the tool timeout and degrades to the other
  channels on failure. Repeated queries are cached instead of sent to the provider again.
- MCP: `openWorldHint` is also set for `external_action` tools, and a missing `mcp` extra
  gives the install hint instead of a traceback.

### Added

- A quant review of the templates and App Factory (`docs/plans/quant-review.md`).
  - **Measured flaws.** The study and the backtest use different trade timing, delisted
    positions lose nothing, and costs ignore drift.
  - **Plan.** Fixes in three tested phases, B-27 to B-29: correct numbers, then one project
    with out-of-sample results, then less to decide.

- App Factory review fixes:
  - **Connection check.** It runs only for built-in templates, and redacts URL-encoded forms
    of secrets too.
  - **TLS.** Generated PostgreSQL connections verify the server certificate by default
    (`sslmode=verify-full`).
  - **Pack drift.** The approval for `create_app` binds a digest of the previewed pack, and a
    pack changed after its preview is refused.
  - **Retries.** `quant-factory check` (the `check_app` tool, approved per call) re-runs an
    existing project's checks.

- Template forks and registered packs (B-26):
  - `quant-factory template fork` copies a built-in profile into your own pack, optionally
    renaming it, then checks and registers it.
  - `template check` loads a pack and plans every profile.
  - `template add` registers a local pack, or a git pack pinned to a tag and cached locally.
  - `template list` shows the built-in and registered templates.
  - Specs can name a registered template.
  - Walkthrough §11: settings, questionnaire, study, backtest, report, and fork, on database
    prices.

- Database guidance (B-25):
  - **The `data-database` component** of `alpha-research` and `backtest` loads prices with
    one read-only query from PostgreSQL, SQLite, or DuckDB.
  - **The generated app** reads credentials only from `DATABASE_URL` or `DATABASE_PASSWORD`,
    ships a `.env.example`, and has a `--check-connection` flag.
  - **Saved connections.** Specs can name a connection saved in `factory.toml`.
    `quant-factory new` offers saved connections and saves new ones.
  - **The factory's check.** `quant-factory create --check-connection` passes only the
    referenced variables and redacts them from the output.

- Guided specs and factory settings (B-24; decisions 0007 and 0008 accepted):
  - **Read-only tools.** `describe_template` lists a template's profiles, components,
    questions, and runtime settings, with secret ones marked. `check_spec` reports `ready` with
    the exact files and the spec as TOML, or every problem.
  - **Commands.** `quant-factory new` asks a template's questions in the terminal.
    `quant-factory serve-mcp` serves the guiding tools to Claude (not `create_app`).
  - **Factory settings.** `quant-factory settings` edits `~/.quant-agent-os/factory.toml`:
    identity, plus connections that hold environment-variable names, never secrets.
  - **Specs.** `AppSpec.settings` holds runtime settings as values or `env:NAME` references.
    Author, email, and license default from the settings file, then `git config`.

- Quant baseline templates (B-23):
  - **Profiles.** App Factory's `quant-baseline` pack has `alpha-research`, `backtest`, and
    `report`, with the kinds of the same names. The projects are portable numpy projects with
    one `metrics` module and known-answer tests.
  - **Bias tests.** Every project checks for look-ahead, the trade lag, and cost ordering, and
    carries a `LIMITATIONS.md`.
  - **Components.** Optional `data-parquet` and `agent-tools`.
- **App specs.** `AppSpec` accepts `template` (a built-in pack, `bundled`, or the path of
  your own pack), `profile`, and `answers`. `kind` stays a shortcut.

- Planned quant templates and a guided App Factory (`docs/plans/app-factory-questionnaires.md`,
  backlog B-23–B-26, proposed decisions 0007–0009):
  - `quant-baseline` templates (`alpha-research`, `backtest`, `report`), with bias tests;
  - user packs;
  - stateless guidance (`describe_template`, `check_spec`, `quant-factory new`);
  - a factory settings file, with secrets as environment-variable names only;
  - database setup;
  - template forks.
- Approval gate (B-09, decision 0005 accepted).
  - **Gate.** `write_local_data`, `execute_code`, `network`, and `external_action` calls need
    a single-use operator approval, bound to the run, the tool, and the input hash. Without
    one, the call records `approval_required`.
  - **Operator path.** `Platform.approve` and `Platform.resume`.
  - **Standing approvals.** `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`, empty by default.
  - **Run store.** Schema 2 adds approvals and upgrades version 1 in place.
- `quant_agent_os.runtime.runner.run_command`: run programs with an allowlisted environment
  and a timeout that kills the process tree.
- `quant_agent_os.testing.check_tool`: the platform's contract checks, for tool packages'
  own tests.
- Quant App Factory foundation (B-10):
  - `AppSpec`, `ArchitecturePlanner`, and the `ScaffoldProvider` port;
  - `PytemplateUvScaffolder`, which uses the pytemplate-uv library (`pytemplate.api`,
    `pytemplate-uv>=3.0,<4`; decision 0006);
  - an `agent-tool` template pack;
  - the `plan_app` tool (read-only, with the exact file preview) and the `create_app` tool;
  - `quant-factory plan` and `quant-factory create`.

- Run persistence (B-08): `quant_agent_os.runs` with in-memory and SQLite run stores, a
  `RunRecorder`, append-only audit events, and redaction of secrets.
  - The orchestrator and the MCP adapter record runs when given a recorder.
  - `quant-research serve-mcp` records every call to `runs.db` (`--runs`, `--no-record`).
  - `quant-research runs` lists runs and shows one run in full.
- SDK facade (B-07): `quant_agent_os.sdk.Platform` with `call`, `run`, `get_run`, and `runs`,
  producing the same records as the MCP path.
- A walkthrough (`docs/walkthrough.md`) from a clean checkout to cited answers in Claude Code
  and Claude Desktop, with recorded runs and known limitations, plus an SDK example
  (`examples/quant-research/sdk_demo.py`).
- An opt-in real-paper evaluation (`packages/quant-research/tests/real_corpus.py`) over 21
  pinned arXiv papers, with gold questions and hand-labelled relations.
- Graph RAG answers carry `path`, the cited chain of edges from the query entity, and the
  `quant-research search` CLI shows it with direction. Graph expansion aggregates parallel
  edges into a `support` count.
- A mock world with an answer key (`tests/mock_world.py`), a graph evaluation
  (`tests/graph_eval.py`, baseline `graph_baseline.json`), and an opt-in scale benchmark
  (`tests/research_bench.py`).

### Fixed

- Extraction (`rules-v4`): negated statements no longer become relations, passive voice no
  longer reverses edges, and "X explains Y" is extracted as `Y explained_by X`. Negation counts
  only right before the relation cue, so "X, which is not small, predicts Y" and "not only"
  keep their relation.
- Ingestion re-processes unchanged files when the pipeline (loader, chunker, extractor, or
  vocabulary version) changes, reported as `reprocessed`. This is schema version 3 and is
  migrated in place.
- Graph paths: equally short paths are chosen by total confidence, independent of seed order.
  Excluding the `vector` channel skips query embedding. `GraphEdge` requires
  `support >= 1`.
- Two-hop expansion no longer slows down with repeated facts: 1.1 s to 14 ms p50 at 3,000
  documents.

- MCP server over stdio: `quant_agent_os.mcp.server` (optional `mcp` extra, built on the
  official MCP Python SDK) forwards `tools/list` and `tools/call` to the existing adapter.
  `quant-research serve-mcp --store PATH` serves the read-only research tools to Claude Code,
  Claude Desktop, and other MCP clients. Tool annotations (read-only, idempotent, open-world)
  are derived from declared permissions.

- uv workspace: Core stays at the root; `packages/quant-research` and
  `packages/quant-app-factory` (skeleton) are members, and a contract test enforces the import
  boundaries.
- Run provenance: `call_id`, `run_id`, `started_at`, `input_sha256`, and `data_versions` on
  every `ToolCallRecord`; `run_id`, `registry_digest`, and `data_versions` in `RunMetadata`;
  `deterministic` and `idempotent` flags on `ToolSpec`.
- Quant Research:
  - Markdown and text ingestion with front matter;
  - the bundled `quant-v1` vocabulary and rule-based extraction;
  - a SQLite knowledge store (FTS5, recursive-CTE graph expansion);
  - hybrid keyword, mention, and graph retrieval with rank fusion;
  - evidence packets that cite every passage and graph edge;
  - the read-only tools `find_related_signals` and `get_source_passage`;
  - the `quant-research` CLI;
  - a retrieval-quality evaluation with a recorded baseline;
  - the `examples/quant-research` demo;
  - an optional vector channel (`Embedder` and `VectorIndex` ports, an offline
    `HashingEmbedder`, exact cosine search in SQLite), off by default after a measured
    no-gain result; `--embedder hashing` enables the mock;
  - hosted embedding models over HTTPS with no new dependency: `VoyageEmbedder` (default
    `voyage-finance-2`) and `OpenAIEmbedder`, chosen with `--embedder voyage|openai` and
    `--embedding-model`. Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` as secrets, and
    query embedding makes `find_related_signals` declare the `network` permission;
  - PDF ingestion with page-level citations through the optional extra
    `quant-agent-os-research[pdf]`, with scanned and corrupt PDFs reported;
  - knowledge-store schema version 2 with in-place migration.

### Changed

- Repositioned the project as Quant-Agent-OS: a platform consumed by AI clients over MCP and the
  SDK, with two products, Quant Research (Graph RAG) and Quant App Factory. Rewrote
  `docs/architecture.md` and `docs/ENGINEERING_REVIEW_PLAN.md` (gap analysis, MVP, 30- and 90-day
  roadmap, backlog). Removed the uv-template planning content; `pytemplate-uv` 3.0 is used as an
  external tool.
- Added proposed decision records 0003 (brand and product structure), 0004 (knowledge store),
  and 0005 (approval gates). Marked the Quant AI Assistant placeholder as superseded.

## [0.1.0] — Unreleased

### Changed

- CI and release workflows use Node.js 24 actions (`actions/checkout@v7`,
  `astral-sh/setup-uv@v10.3.2`, `actions/upload-artifact@v7`, `actions/download-artifact@v8`).

### Added

- A tool contract (`ToolSpec`, `@tool`) with typed pydantic input and output, declared
  permissions, timeouts, and output-size limits; an explicit `ToolRegistry`; and a read-only-by-
  default `PermissionPolicy`.
- `ToolExecutor`, which records every call (ok, invalid input, denied, unknown tool, timeout,
  error) with its version and duration.
- A single bounded `Orchestrator`: provider-proposed plans are validated against the registry,
  allowlist, and step limit before execution.
- `Evidence` and `SourceRef` models that keep computed, retrieved, and interpretation statements
  distinct; structured `Report` output with Markdown rendering.
- A `ModelProvider` protocol and `FakeModelProvider`; `QUANT_AGENT_OS_*` settings with secret
  handling; log redaction.
- An MCP adapter (`tool_definitions`, `call_tool`) over the same executor.
- The `quant-agent-os` CLI: `info`, `plan-schema`, `--version`.
- An offline `examples/portfolio-research` workflow on synthetic data.
- Architecture, configuration, security, and getting-started docs; decision records 0001 and
  0002; and the QuantFoundry engineering plan, moved here from pytemplate-uv.
