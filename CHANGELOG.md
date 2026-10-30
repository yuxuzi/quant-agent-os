# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
    no-gain result;
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
