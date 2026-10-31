# Quant-Agent-OS architecture

This is the canonical architecture of Quant-Agent-OS. The roadmap, gap analysis, and backlog are
in [ENGINEERING_REVIEW_PLAN.md](ENGINEERING_REVIEW_PLAN.md). Decisions are recorded in
[decisions/](decisions/).

Every design element carries one of three labels:
- **Confirmed** means it exists in this repository today and is tested.
- **Proposed** means it is the agreed target design and is not built yet.
- **Open** means it is undecided; the open question is listed in the plan.

## 1. Positioning

**Quant-Agent-OS** is the brand, the product, and the platform: an open-source quantitative
intelligence and application-engineering platform. It is **not a chatbot**. Claude, GitHub
Copilot, Cursor, and other AI clients consume its capabilities through MCP, a REST API, and a
Python SDK. Quant-Agent-OS supplies the tools, workflows, quantitative services, and provenance;
the client supplies the conversation.

| Kind | Name | Role | Status |
| --- | --- | --- | --- |
| Platform | Quant-Agent-OS Core (`quant_agent_os`) | Runtime, tool registry, executor, orchestrator, evidence, interfaces | Confirmed (partial) |
| Product A | Quant Research (`quant_research`) | Graph RAG research and knowledge system with source provenance | Proposed |
| Product B | Quant App Factory (`quant_app_factory`) | Requirements to tested, packaged Python applications | Proposed |
| Domain service | Quant analytics (separate package; name open) | Deterministic performance, risk, and later backtest calculations | Proposed |
| External tool | [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) 3.0 | Standalone, deterministic project scaffolder | Confirmed (published) |

The baseline documents call the scaffolder "template-uv" or "uv-template"; its published name is
`pytemplate-uv`. It is not a Quant-Agent-OS product or component. Quant App Factory calls it
through a replaceable adapter ([section 9](#9-pytemplate-uv-integration-boundary)).

"QuantFoundry" (the earlier umbrella name) and "Quant AI Assistant" (the earlier flagship app)
are retired by [decision 0003](decisions/0003-brand-and-product-structure.md). The assistant's
portfolio-research workflow survives as a platform workflow that external clients call. All
names remain provisional until availability checks are done.

## 2. System context

```text
        Claude · Copilot · Cursor · VS Code agents · scripts · notebooks
                                   │
              MCP (stdio, later HTTP) · REST (later) · Python SDK · CLI
                                   │
 ┌─────────────────────────────────▼─────────────────────────────────────┐
 │                      Quant-Agent-OS Core platform                     │
 │  tool contract & registry · permission & approval policy · executor   │
 │  orchestrator · evidence & reports · run store & audit · config       │
 │  model-provider ports · observability                                 │
 └───────────────▲───────────────────────────────────▲───────────────────┘
                 │ registers tools                   │ registers tools
     ┌───────────┴────────────┐          ┌───────────┴─────────────┐
     │     Quant Research     │          │    Quant App Factory    │
     │ ingestion · knowledge  │          │ AppSpec · architecture  │
     │ graph · hybrid         │          │ scaffold · generate ·   │
     │ retrieval · evidence   │          │ validate · package      │
     └───────────┬────────────┘          └───────────┬─────────────┘
                 │                                   │ ScaffoldProvider port
     KnowledgeStore adapters               pytemplate-uv adapter (uvx, JSON)
     (memory · SQLite · PostgreSQL)                  │
                                          pytemplate-uv 3.0 (independent)

   Quant analytics (independent package): registered as tools by its owner
   User-owned: models and keys, documents, data, databases (bring your own)
```

Arrows into Core mean registration: products hand Core `ToolSpec`s. Core never imports a
product.

## 3. Core platform components

| Component | Module | Responsibility | Status |
| --- | --- | --- | --- |
| Tool contract | `tools/contracts.py` | `ToolSpec`, `@tool`, descriptors | Confirmed |
| Registry | `tools/registry.py` | Explicit, unique registration and lookup | Confirmed |
| Permissions | `tools/permissions.py` | `Permission`, `PermissionPolicy`, allowlist, read-only default | Confirmed |
| Plan models | `runtime/plan.py` | `Plan`, `PlanStep` | Confirmed |
| Executor | `runtime/executor.py` | Permissions, validation, timeout, output size, `ToolCallRecord` | Confirmed |
| Orchestrator | `runtime/orchestrator.py` | One bounded plan, validate, execute, report | Confirmed |
| Evidence | `evidence/models.py` | `Evidence` (`retrieved` · `computed` · `interpretation`), `SourceRef` | Confirmed |
| Reporting | `reporting/` | `Report`, `RunMetadata`, Markdown rendering | Confirmed |
| Providers | `providers/` | `ModelProvider` protocol, `FakeModelProvider` | Confirmed; no real adapter |
| Configuration | `config/settings.py` | `QUANT_AGENT_OS_*` settings, `SecretStr` | Confirmed |
| Observability | `observability/logging.py` | Logging with secret redaction | Confirmed |
| MCP adapter | `mcp/adapters.py` | `tool_definitions`, `call_tool` over the executor | Confirmed; no transport |
| CLI | `cli.py` | `info`, `plan-schema`, `--version` | Confirmed |
| Approval gate | `tools/approvals.py` | Approval requests and grants for consequential permissions | Proposed |
| Run store and audit | `runs/` | Persisted runs, call records, append-only audit events (approvals: proposed) | Confirmed |
| MCP server | `mcp/server.py` | stdio transport over the adapter (optional `mcp` extra); tool annotations from declared permissions | Confirmed |
| SDK client | `sdk/` | `Platform` facade: registry assembly, `call`, `run`, run lookup | Confirmed |
| REST API | `api/` | FastAPI over the same executor (optional `api` extra) | Proposed, deferred |
| Jobs | `runs/jobs.py` | Status for long-running calls (`pending` · `running` · `done`) | Proposed, deferred |

Core builds only the interfaces the first vertical slice needs. Each proposed row names the
milestone that pulls it in (see the plan).

## 4. Layering, dependency direction, and package ownership

### 4.1 Layers inside each package

| Layer | Contains | Must not depend on |
| --- | --- | --- |
| Domain | Pydantic models, invariants, `Protocol` ports (for example `KnowledgeStore`) | Any framework, transport, database, or model vendor |
| Application | Use-case services (for example `ResearchService`, `FactoryService`) | Transports and concrete adapters |
| Infrastructure | Adapters: SQLite, PostgreSQL, model providers, subprocess, filesystem | Interfaces |
| Interface | `@tool` wrappers, MCP server, REST routes, CLI, SDK | Infrastructure internals (wired at bootstrap) |

### 4.2 The service → tool → executor chain

**Confirmed** today for the example analytics tools; **proposed** for products. This is how the
baseline's rule that "MCP tools are adapters over application services" fits the existing
contract:

```text
MCP client ─┐
REST client ┼─▶ interface adapter ─▶ ToolExecutor ─▶ ToolSpec(@tool wrapper) ─▶ Application service
SDK / CLI  ─┤      (no logic)        permissions·approval                         │
Orchestrator┘                        validation·limits·record                     ▼
                                                                       Domain models and ports
                                                                                   │
                                                                       Infrastructure adapters
```

- An application service is an ordinary class with typed methods. It is unit-tested without
  the platform.
- A `@tool` wrapper is a few lines that call one service method and map its result to an output
  model carrying `warnings` and `evidence`.
- Every remote or agent path (MCP, REST, orchestrator, and SDK `call`) goes through
  `ToolExecutor`. Permissions, approvals, validation, timeouts, size limits, and records
  therefore apply identically on every path. Contract tests assert this
  ([decision 0002](decisions/0002-mcp-boundary.md)).
- Trusted in-process code may import a service directly as a library. That path is outside the
  agent boundary and produces no audit record.

### 4.3 Package ownership and allowed imports

| Package | Owns | May import | Distribution |
| --- | --- | --- | --- |
| `quant_agent_os` (repo root) | Core | pydantic, typer, rich; optional extras | `quant-agent-os` |
| `quant_research` (`packages/quant-research`) | Quant Research | `quant_agent_os` public API | `quant-agent-os-research` (name open) |
| `quant_app_factory` (`packages/quant-app-factory`) | Quant App Factory | `quant_agent_os` public API | `quant-agent-os-factory` (name open) |
| Quant analytics (own repository) | Deterministic metrics | Numerical libraries only | Name open |
| `pytemplate-uv` (own repository) | Scaffolding | Nothing in this ecosystem | `pytemplate-uv` |

Rules:
- Core imports no product.
- The products do not import each other. If they need to collaborate, they do it through a
  platform workflow (an orchestrated plan that uses both products' tools) or through documented
  models in Core.
- Nothing imports `pytemplate-uv`; it is invoked as a CLI.
- The analytics package imports no agent or LLM library.
- An import-linter-style test enforces these rules (backlog item B-03).

Repository strategy (**proposed**, [decision 0003](decisions/0003-brand-and-product-structure.md)):
one repository and one uv workspace. Core stays at the root, where it already works, and each
product is a workspace member with its own `pyproject.toml`, tests, version, and changelog. This
keeps Core and the products independently installable and testable without moving functioning
code. Analytics and `pytemplate-uv` keep their own repositories and release cycles.

Core's internal import order is **confirmed**:
`tools` → `runtime.plan` / `runtime.executor` → `evidence` / `reporting` →
`runtime.orchestrator` → `providers`, `config`, `mcp`, `observability`, `cli`.

## 5. Tool contract and request lifecycle

### 5.1 Contract (confirmed)

A `ToolSpec` has the following fields:
- a snake_case name and a description;
- a version;
- pydantic input and output models;
- the plain function;
- the `Permission`s it declares;
- `timeout_seconds` and `max_output_bytes`.

The `@tool` decorator builds one from a typed function, and that function stays directly
callable. Output models expose `warnings` and `evidence`, and reports collect both.

### 5.2 Additive contract fields (proposed, backlog B-02)

The baseline lists what each tool must specify. The fields below cover what the current contract
lacks. Every field has a default, so existing tools keep working.

| Field | Default | Purpose |
| --- | --- | --- |
| `deterministic: bool` | `True` | Same input and data version give the same output; reports flag non-deterministic calls |
| `idempotent: bool` | `True` | Safe to retry; non-idempotent tools are never retried automatically |
| `long_running: bool` | `False` | Runs as a job with status polling (deferred until a tool needs it) |

Approval is **derived, not declared**. A tool needs approval when it declares a consequential
permission (see [section 12](#12-security-and-approval-model)). The error model is the existing
`CallStatus` set (`ok`, `invalid_input`, `denied`, `unknown_tool`, `timeout`, `error`) plus a
proposed `approval_required` status.

### 5.3 Request lifecycle (confirmed, with proposed additions marked)

1. **Plan.** The orchestrator sends the provider the goal and the descriptors of the tools it
   allows, then parses the reply into a `Plan`.
2. **Validate.** The plan is rejected before anything runs if it is malformed, exceeds
   `max_steps`, or names a tool that is unregistered or outside the allowlist.
3. **Approve** (proposed). If any step needs approval, the run stops with status
   `awaiting_approval`. It resumes only after an operator approves the exact steps.
4. **Execute.** For each step the executor checks permissions, validates the input, runs the
   tool with a timeout, then validates the output and enforces its size limit. Failures become
   records, not exceptions.
5. **Report.** `build_report` collects evidence and warnings, turns failed calls into warnings,
   and adds limitations and run metadata.
6. **Persist.** With a `RunRecorder`, the run, its records, and audit events go to the run
   store as they happen, including runs whose plan is rejected. Approvals are proposed.

Tool output and retrieved text are never fed back into **planning**. A tool may give retrieved
text to a model for synthesis (section 6.5). The synthesized text is labelled `interpretation`
and cannot choose tools.

## 6. Quant Research: Graph RAG architecture

**Confirmed** for keyword, mention, graph, and mock-vector retrieval over SQLite, and for
Markdown, text, and PDF ingestion (`packages/quant-research`; step plan in
[plans/quant-research-retrieval.md](plans/quant-research-retrieval.md)). **Proposed** for a
real embedding model, PostgreSQL with pgvector, the LLM extractor, OCR, and server-side
synthesis.

Graph RAG is the defining capability of Quant Research: answers are assembled from explicit,
typed relationships between quantitative concepts, plus the passages that justify each
relationship, not from vector similarity alone.

### 6.1 Pipeline

```text
 operator (CLI / SDK; never an agent tool in the MVP)
   │  quant-research ingest ./corpus
   ▼
 Loaders ─▶ Chunker ─▶ Extractors ───────────────▶ Entity resolution ─▶ KnowledgeStore
 md, txt,    passages    rule-based (vocabulary,     canonical ids,        documents, chunks,
 PDF text,   with exact  frontmatter, citations)     alias merge           entities, mentions,
 code,       locations   LLM extractor (later,                             relations, corpus
 notebooks               opt-in, labelled)                                 snapshot
                                                                               │
 MCP / SDK tool calls ─▶ ResearchService.query ─▶ Hybrid retrieval ◀───────────┘
                                                  keyword · graph expansion · vector (later)
                                                        │ rank fusion
                                                        ▼
                                                  EvidencePacket ─▶ client LLM or report
```

### 6.2 Ingestion (operator-only)

- Ingestion writes to the store. It is therefore an operator command (CLI or SDK), not an agent
  tool, in the MVP. This keeps agents read-only.
- Each document records a content `sha256`, a source URI, a title, authors, a date, a license
  note, and the loader version.
- Each chunk keeps an exact location. For Markdown and text this is the file line range and
  section, with paragraph chunks. For PDF it is the page and the line range of that page's
  extracted text. Lines are packed into chunks of about 500 characters that close at sentence
  ends, because extraction drops blank lines.
- Loaders are chosen by suffix. PDF support is the optional `pdf` extra (pypdf). Without it,
  and for scanned, encrypted, or corrupt PDFs, the file is skipped or flagged with a warning
  that says why.
- A **corpus snapshot** id is a hash over document hashes plus extractor versions. Every answer
  records the snapshot it used, so results can be reproduced.

### 6.3 Extraction

The extractors in the first version are deterministic:
- a curated, versioned quant vocabulary (`quant-v1`, bundled) of factors, signals,
  strategies, methods, metrics, asset classes, datasets, and concepts, with aliases (for
  example "HML" → `factor:value`). The longest alias wins, and short all-capital aliases match
  case-sensitively;
- document front matter (title, authors, published, license);
- cue phrases between two entities in the same sentence (`rules-v4`). For example, "X predicts
  Y" yields `X predicts Y`. Passive and inverse forms ("Y is predicted by X", "X explains Y")
  swap subject and object, so edges point the right way. A negation in the three words right
  before the cue ("does not predict", "fails to explain") suppresses the pair entirely. A
  negation elsewhere ("X, which is not small, predicts Y") and "not only" do not. Without a
  cue, the pair becomes a `related_to` edge with confidence 0.4, which the default
  `min_confidence` of 0.5 excludes, unless a negation such as "unrelated to" sits between
  the entities;
- citation and reference parsing (planned).

Every extracted mention and relation stores the extractor name, version, confidence, and the
chunk it came from. An LLM extractor is a later, opt-in adapter. Its relations are labelled
`extractor = "llm:<model>"`, and an evaluation must show that it improves recall before it is
enabled by default.

### 6.4 Retrieval

1. **Seed:** match vocabulary entities in the query. When the query names none, seed from at
   most 3 entities in the best keyword passage, report them as direct (`hops = 0`) results, and
   warn.
2. **Keyword channel:** BM25 over chunks (SQLite FTS5; PostgreSQL full-text search later),
   using the query's content terms with stopwords removed. Without FTS5, a term-overlap scorer
   is used.
3. **Mention channel:** chunks that mention the most seed entities, so passages that define the
   topic rank well.
4. **Graph channel:**
   - **Walk.** Traverse up to `hops` (default 1, maximum 2) from the seeds over edges with
     `confidence >= min_confidence`. A recursive CTE computes entity distances first, then a
     second query returns the edges. This works on SQLite and PostgreSQL.
   - **Aggregate.** A fact stated in many chunks returns at most 3 citing edges and a
     `support` count.
   - **Score.** Each distinct fact contributes confidence divided by hops, times
     `1 + log2(support)`.
   - **Path.** Each related entity carries `path`, the shortest chain of cited edges from a
     seed. Among equally short paths, from any seed, the highest total confidence wins,
     independent of traversal order. Every path edge is cited in the evidence, even when its
     chunk is not among the top `k` passages. A two-hop answer therefore shows how it connects, for
     example `trend-following -uses-> time-series-momentum -extends-> momentum`.
   - **Rank.** The results are capped at 10 entities. Path and link chunks form the ranking.
   - **Benchmark.** On the mock world at 3,000 documents and 15,000 relations, two-hop
     queries take 14 ms at p50 and 37 ms at p95 (`research_bench.py`).
5. **Vector channel** (optional, off by default): cosine similarity between the query
   embedding and the chunk embeddings of the same model, keeping hits of at least 0.2. The
   `Embedder` port has three adapters:
   - `HashingEmbedder`, an offline mock (feature hashing over words, word pairs, and
     vocabulary entities), used in tests;
   - `VoyageEmbedder`, where `voyage-finance-2` is the default and any current Voyage model is
     allowed;
   - `OpenAIEmbedder`, using `text-embedding-3-small` or `-large`.

   The hosted adapters call each provider's HTTPS endpoint with the standard library. They
   batch under the documented limits, retry 429 and 5xx with backoff, honour `Retry-After`, and
   normalise vectors to unit length. Voyage receives `input_type` `document` or `query`.

   Select an embedder with `open_research(embedder=...)` or `--embedder`. The model id
   (`voyage:voyage-finance-2:1024`) is part of the corpus snapshot. Searching with a model the
   store has no embeddings for warns instead of silently dropping the channel. The channel
   becomes a default only after it beats the evaluation baseline, which includes
   `paraphrase_hit_rate`. pgvector replaces the SQLite index at scale.
6. **Fuse:** reciprocal rank fusion (k = 60) over the channels, with ties broken by chunk id
   for determinism, capped at `k` passages.
7. **Package:** return an `EvidencePacket` containing passages (with `SourceRef`), the graph
   paths whose edges cite their supporting chunks, the matched entities, the snapshot id, and
   warnings (for example "no source supports relation X directly").

### 6.5 Synthesis boundary

- In the MVP, **the client LLM synthesizes**. Quant Research returns evidence packets, and
  Claude (or another client) writes the answer, so the MVP makes no server-side model calls.
- Server-side synthesis (`research_alpha`) is a later, optional tool. It uses a `ModelProvider`,
  needs the `network` permission when the provider is remote, and returns `interpretation`
  evidence that cites the passages it used. Hypotheses are always `interpretation`.
- Retrieved documents are untrusted. Fixture documents containing injected instructions must
  not change plans, tool selection, or permissions (an evaluation suite).

### 6.6 Domain model

The entity types are:
- `paper`, `author`, `concept`, `factor`, `signal`, `strategy`, `instrument`, `asset_class`,
  `method`, `dataset`, `metric`, `empirical_result`.

The `rules-v2` predicates are:
- `predicts`, `correlates_with`, `explained_by`, `extends`, `contradicts`, `uses`,
  `tested_on`, `related_to`.

The type and predicate lists live in `quant_research.domain.models`. A change to either, to
the vocabulary, or to the extractor bumps the matching version, and therefore the corpus
snapshot.

### 6.7 Quality evaluation

`packages/quant-research/tests/research_eval.py` scores a gold set
(`tests/evaluation/gold.toml`) on the following metrics:
- related-entity recall;
- passage precision@k;
- citation validity (every link's chunk mentions both entities);
- location validity (every line range reproduces its text);
- empty-query accuracy.

Tests fail on any drop below `baseline.json`, and the two validity metrics must be 1.0. The
current gold set is small and was written alongside the code, so a held-out set is needed
before making quality claims.

`tests/graph_eval.py` scores the graph against a mock world with a known answer key
(`tests/mock_world.py`, plan [graph-rag-mock-world.md](plans/graph-rag-mock-world.md)). It
measures:
- extraction precision, recall, and F1 per predicate;
- negation false positives and direction errors;
- related recall;
- evidence recall and precision@3, with and without the graph channel;
- path validity.

The baseline is `graph_baseline.json`.

## 7. Database and knowledge-graph design (proposed)

The relational schema is shared by every adapter. A dedicated graph database is evaluated only
if traversal depth, scale, or query patterns exceed what recursive CTEs handle
([decision 0004](decisions/0004-knowledge-store.md)).

| Table | Key columns |
| --- | --- |
**Confirmed** in `SqliteKnowledgeStore` (schema version 2; version-1 stores migrate in
place):

| Table | Key columns |
| --- | --- |
| `documents` | `id` (`doc:` + hash prefix), `sha256` (unique), `uri` (unique), `title`, `authors`, `published`, `license_note`, `loader_version` |
| `chunks` | `id` (`<document>#<ordinal>`), `document_id`, `ordinal`, `text`, `line_start`, `line_end`, `section`, `page` |
| `chunks_fts` | FTS5 index of `chunks.text` (when the SQLite build has FTS5) |
| `entities` | `id` (`type:slug`), `type`, `name`, `aliases` |
| `mentions` | `entity_id`, `chunk_id`, `start`, `end`, `extractor`, `confidence` |
| `relations` | `id`, `subject_id`, `predicate`, `object_id`, `chunk_id`, `extractor`, `extractor_version`, `confidence` |
| `corpus_snapshots` | `seq`, `id`, `document_count`, `vocabulary_version`, `extractor_versions` |
| `embeddings` | `chunk_id`, `model`, `dimensions`, `vector` (float32 blob); cascades with its chunk |

Deleting a document cascades to its chunks, mentions, and relations. A changed file therefore
replaces all of its records.

Consistency rules:
- Ingestion builds and publishes the corpus snapshot in one `BEGIN IMMEDIATE` transaction, so
  concurrent ingestions cannot publish a stale snapshot.
- Search reads run in one read transaction (`KnowledgeStore.reading()`), so an answer never
  mixes two corpus versions.
- Migrations (schema version 3) are atomic and can be re-run.
- Each document records its `pipeline`, the loader, chunker, extractor, and vocabulary
  versions. Ingestion re-processes unchanged files whose pipeline differs, so upgrading the
  rules never leaves stale relations behind.
- Serving opens the store read-only (`mode=ro`) and never migrates it.

`KnowledgeStore`, `VectorIndex`, `Embedder`, and `SourceLoader` are `Protocol`s. The adapters
are:
- `SqliteKnowledgeStore`. It uses only the standard library, works as a file or as
  `":memory:"` (for tests), and shares one locked connection because the executor runs tools on
  worker threads.
- `TextFileLoader`, for Markdown and text with front matter, and `PdfLoader`, for PDF through
  pypdf.
- `HashingEmbedder`, the offline mock embedder. `SqliteKnowledgeStore` also implements
  `VectorIndex` with exact brute-force cosine, which is fine for thousands of chunks.
- DuckDB was considered and not chosen for this store. It suits the later analytics and
  backtest service (columnar scans over factor and price data) better than this workload of
  point lookups and graph traversal.
- `PostgresKnowledgeStore`, which uses SQLAlchemy, psycopg, and pgvector, ships as an optional
  extra, and is planned for days 31–60.

Run and audit tables are in [section 11](#11-workflow-state-audit-and-provenance).

## 8. Quant App Factory architecture (proposed)

The factory turns requirements into maintainable, tested Python applications. It does not
produce loose code snippets.

```text
requirements ─▶ AppSpec (typed, editable, validated)
                  │  drafted by a model (later), or written as TOML/JSON (MVP)
                  ▼
               ArchitecturePlanner (deterministic rules: app kind → pattern, profile,
                  │                 components, layers, dependencies)
                  ▼
               ScaffoldProvider.plan()    read-only preview: files, dependencies
                  │  approval (write_local_data)
                  ▼
               ScaffoldProvider.create()  pytemplate-uv adapter
                  ▼
               CodeGenerator (later)      model-written modules in designated files only,
                  │                       approval (write_local_data)
                  ▼
               Validator                  uv sync · ruff · ruff format · mypy · pytest
                  │                       subprocess with timeout and kill, approval
                  ▼                       (execute_code)
               FactoryReport              files, checks, versions, request, provenance
```

The pieces:
- **`AppSpec`** holds the name, kind (`agent-tool`, `python-cli`, `fastapi-service`, and later
  `factor-library`), purpose, inputs and outputs, required capabilities, and constraints. It is
  the single, reviewable contract. The model never writes files directly.
- **`ArchitecturePlanner`** is deterministic and unit-tested. It recommends a pattern and maps
  the spec to a scaffold request.
- **Template pack.** `agent-tool` and `mcp-server` profiles live in a pytemplate-uv template
  pack inside `quant_app_factory`. That pack encodes the Quant-Agent-OS tool contract, so
  `pytemplate-uv` itself stays generic.
- **Generated apps are portable.** `python-cli` and `fastapi-service` apps have no dependency on
  Quant-Agent-OS. `agent-tool` packages depend on `quant-agent-os` on purpose, because they are
  tools for the platform.
- **Iteration** (fix → re-validate) feeds diagnostics back to a model. This is outside the
  "no feedback into planning" invariant only if the loop is bounded (at most N rounds), uses
  structured diagnostics (rule ids, file:line, test ids), and asks for approval for each write.
  It needs a decision record before it is built (open).

## 9. pytemplate-uv integration boundary

**Confirmed:** `pytemplate-uv` 3.0 is published. It provides `plan`, `validate`, `create`, and
`create-from-config` with `--json` and stable exit codes, `schema request`, and template packs
through `--template` and `--template-ref`.

**Proposed** contract inside `quant_app_factory`:

```python
class ScaffoldProvider(Protocol):
    name: str

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan: ...  # no writes
    def create(self, request: ScaffoldRequest, approval: Approval) -> ScaffoldResult: ...
```

`PytemplateUvScaffolder` implements this protocol:
- It writes the request as TOML and runs
  `uvx pytemplate-uv==3.0.* {plan|create-from-config} <file> --json`.
- It applies a timeout and maps exit codes to typed errors.
- It parses the JSON result.

Further rules:
- The version range is pinned in one constant.
- A contract test runs against a recorded JSON fixture offline, and a smoke test against the real
  CLI is opt-in.
- No Python import of `pytemplate-uv` exists, no dependency on it is declared, and nothing of its
  implementation is copied.
- Another scaffolder (Copier directly, or a cookiecutter-based one) can be swapped in by writing
  a second `ScaffoldProvider`.
- `pytemplate-uv` releases and plans are its own repository's business.

## 10. Interface strategy: MCP, REST, SDK, CLI

| Interface | Audience | Built on | Status |
| --- | --- | --- | --- |
| MCP (stdio) | Claude Desktop/Code, Cursor, Copilot agents | `quant_agent_os.mcp.server` (official MCP Python SDK, optional `mcp` extra) over the adapter; `quant-research serve-mcp` | Confirmed |
| Python SDK | Notebooks, scripts, other services | `quant_agent_os.sdk.Platform` facade over registry + executor + run store | Confirmed |
| CLI | Operators | Typer: `quant-agent-os info`/`serve-mcp`; `quant-research ingest`/`search`; `factory plan/create` | Partly confirmed |
| REST | Web apps, non-Python clients, the hosted offering | FastAPI over the executor (optional extra) | Deferred until a client needs it |

The rules that apply to every interface:
- Interfaces contain no business logic.
- Tool names and schemas are identical across interfaces.
- MCP and REST results equal the direct executor results (contract tests).
- MCP stdio runs as the local user with the operator's granted permissions. HTTP transports need
  authentication (section 12).

### Candidate public tools (staged)

The tool names come from the baseline document. A tool is exposed only when its service has
tests.

| Tool | Product or service | Permissions | Stage |
| --- | --- | --- | --- |
| `find_related_signals` | Quant Research | `read_local_data`, plus `network` when a hosted embedder embeds the query | MVP |
| `get_source_passage` | Quant Research | `read_local_data` | MVP |
| `analyze_performance` | Analytics (example tools today) | `read_local_data` | MVP |
| `generate_report` | Core (orchestrated run → `Report`) | inherits the plan's tools | MVP (SDK/CLI); MCP at days 31–60 |
| `plan_app` | App Factory | `read_local_data`, `execute_code` (runs `uvx`) | Days 22–30 |
| `create_app` | App Factory | adds `write_local_data` → approval | Days 22–30 |
| `research_alpha` | Quant Research | adds `network` if the model is remote | Days 31–60 |
| `generate_strategy` | App Factory + Research | `read_local_data` | Days 31–60 |
| `generate_factor_code` | App Factory | `write_local_data`, `execute_code` → approval | Days 61–90 |
| `analyze_risk` | Analytics | `read_local_data` | Days 61–90 |
| `run_backtest` | Backtest service (new package) | `read_local_data`, `long_running` | After day 90 unless pulled forward |

## 11. Workflow state, audit, and provenance

**Confirmed:**
- `ToolCallRecord` stores the tool, version, arguments, status, output or error, and duration.
- `RunMetadata` stores the platform version, provider, and start and end times.
- `Evidence` and `SourceRef` keep facts and interpretation apart.

**Confirmed** (B-02, B-07, B-08):
- `ToolCallRecord` also stores `call_id`, `run_id`, `started_at`, `input_sha256`, and
  `data_versions` (for example the corpus snapshot id).
- `RunMetadata` stores the `run_id`, the `registry_digest` (a hash of tool names, versions,
  and schemas), and the merged data versions.
- `quant_agent_os.runs` has a `RunStore` protocol with in-memory and SQLite adapters
  (standard library), and a `RunRecorder`.
  - **Tables:** `runs` (kind `call` or `plan`; status `running`, `completed`, or `failed`,
    with `planned` and `awaiting_approval` reserved for approvals), `tool_calls`, and
    `audit_events`.
  - **Every call is a run.** Each MCP or SDK call is a one-call run, and each orchestrated
    plan is a run.
  - **Redaction.** Arguments and outputs pass through `redact` before storage: `SecretStr`
    values, and values under keys such as `api_key` or `token`.
  - **Failures.** A store failure is logged and never fails the call.
- `audit_events` is append-only (SQLite triggers reject UPDATE and DELETE), with columns
  `run_id`, `actor`, `event`, `subject`, `at`, and `details`. The details hold ids, hashes,
  and statuses, never inputs or secrets. This table is the future basis for usage metering.
- `quant-research serve-mcp` records to `runs.db` beside the knowledge store by default
  (`--runs`, `--no-record`). `quant-research runs [RUN_ID]` lists runs or shows one.

**Proposed:**
- an `approvals` table (decision 0005);
- a digest of the public settings, and package versions in `RunMetadata`.

## 12. Security and approval model

**Confirmed:**
- Read-only by default: only `read_local_data` is granted.
- An explicit registry and an optional allowlist.
- Bounded plans and bounded calls.
- No feedback into planning.
- `SecretStr` and log redaction.

See [security.md](security.md) for details.

**Proposed** ([decision 0005](decisions/0005-approval-gates.md)):
- `write_local_data`, `execute_code`, `network`, and `external_action` are **consequential**.
  Granting such a permission in settings makes a tool eligible to run. Each call still needs an
  **approval** that is bound to the run id, the tool name, and the input hash, and that expires
  after use.
- Approvals come from the operator through the CLI or SDK, or from the MCP client's own
  confirmation step when the operator configures that. The model can never grant an approval.
- `execute_code` tools run through a subprocess runner with a working-directory jail,
  environment scrubbing (no secrets passed through), a timeout, and a hard kill. A thread
  timeout is not enough here, because it cannot stop a running process.
- Ingestion and other store writes are operator commands, not agent tools.
- HTTP interfaces (later) need authentication: API keys with scopes first, then OIDC. They also
  carry a `principal` on every audit event. Tenancy is a column in the run store from day one,
  but multi-tenant hosting remains out of scope.
- Still out of scope: trading, brokerage connectivity, unrestricted code execution, and
  autonomous external actions.

## 13. Deployment architecture

| Mode | What runs | Status |
| --- | --- | --- |
| Local library | `uv add quant-agent-os`; SDK in a notebook or script | Confirmed (core) |
| Local MCP server | `uv run --directory <repo> quant-research serve-mcp --store <abs path>`, launched by Claude Desktop or Code over stdio; read-only; the SQLite store is created by `quant-research ingest` | Confirmed |
| Team server | Docker image, PostgreSQL + pgvector, MCP over HTTP and REST, API-key auth | Proposed, days 61–90 |
| Hosted | Managed, multi-tenant | Out of scope; commercial option later |

Not planned without a concrete need: a message broker, a distributed workflow engine,
Kubernetes, or several model frameworks.

## 14. Extension points (confirmed)

- **Models:** implement `ModelProvider` (`name`, `generate(prompt) -> str`). Each vendor ships
  as an adapter with its own optional dependency, and tests stay on `FakeModelProvider`.
- **Tools:** register `ToolSpec`s from your own packages in an explicit `ToolRegistry`. There is
  no plugin discovery and no dynamic code loading.
- **MCP:** a transport only forwards `tools/list` and `tools/call` to `tool_definitions` and
  `call_tool`.

Extension points are `KnowledgeStore` and `RunStore` (confirmed), and `ScaffoldProvider` and
`EmbeddingProvider`. Each has a Protocol, one reference adapter, and contract tests.

## 15. Not in scope

The following are not in scope:
- multi-agent orchestration without an evaluation ([decision 0001](decisions/0001-one-orchestrator-first.md));
- autonomous trading or external actions;
- unrestricted code execution;
- hosted multi-tenant infrastructure;
- a mandatory vector or graph database;
- broad connector coverage;
- a plugin marketplace.
