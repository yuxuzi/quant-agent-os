# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Python 3.15 is supported and tested in CI (3.11–3.15 on Linux, macOS, and Windows).

### Fixed

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
