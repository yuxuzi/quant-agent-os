# Quant-Agent-OS architecture

How the system is built today. Why it is built this way is in [decisions/](decisions/), and what is
still open is in [ENGINEERING_REVIEW_PLAN.md](ENGINEERING_REVIEW_PLAN.md).

## Positioning

Quant-Agent-OS gives AI clients quantitative research tools with cited, computed evidence, over MCP,
REST, and a Python SDK. It is not a chatbot, and it calls no model: the client supplies the conversation
and the plan ([decision 0016](decisions/0016-clients-orchestrate.md)). The expected customer is a firm
or team that hosts it themselves. One wheel holds five import names
([decision 0018](decisions/0018-one-wheel.md)), and `qaos` is the one command.

| Package | Role | Extra |
| --- | --- | --- |
| `quant_agent_os` | Core: tool contract, executor, evidence, runs, interfaces | base |
| `quant_research` | Cited keyword search with entity linking and a small graph of extracted facts, plus optional semantic search | none; `pdf`, `postgres`, `local-embeddings` add to it |
| `quant_analytics` | Performance and risk metrics ([decision 0010](decisions/0010-analytics-on-empyrical.md)) | `analytics` |
| `quant_app_factory` | Generates tested Python projects, through [`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) | `factory` |
| `quant_platform` | The composition root: `qaos`, one server for every toolset, `research_report`, prompts, sign-in, and approvals | `server` |

## System context

```text
        Claude · Copilot · Cursor · VS Code agents · scripts · notebooks
                                   │
              MCP (stdio, Streamable HTTP) · REST · Python SDK · CLI
                                   │
 ┌─────────────────────────────────▼─────────────────────────────────────┐
 │  quant_platform: composes the toolsets below into one server          │
 ├───────────────────────────────────────────────────────────────────────┤
 │  quant_agent_os (Core)                                                │
 │  tool contract & registry · permissions & approvals · bounded executor│
 │  evidence & reports · run store & audit · config · observability      │
 └─────▲─────────────────────────▲─────────────────────────▲─────────────┘
       │ registers tools         │ registers tools         │ registers tools
 ┌─────┴──────────┐      ┌───────┴────────┐      ┌─────────┴──────────┐
 │ Quant Research │      │ Quant Analytics│      │ Quant App Factory  │
 └─────┬──────────┘      └───────┬────────┘      └─────────┬──────────┘
 KnowledgeStore adapters    empyrical-reloaded      ScaffoldProvider port
 (SQLite · PostgreSQL)                              pytemplate-uv adapter

 User-owned: documents, data, databases, and any embedding key
```

Arrows into Core mean registration: products hand Core `ToolSpec`s, and Core never imports a product.

Identity is an API key with scopes on the HTTP server. Scopes are `tools:list`, `tools:call`, and
`runs:approve`; there are no PM, trader, or researcher roles. Each firm runs its own installation, and
there is no multi-tenant service ([decision 0023](decisions/0023-no-hosted-service.md)). OAuth and the approval page are in
`quant_platform.identity`, for a firm's own server. Core keeps a `tenant` column (default `local`) and
small hooks such as `UsageGate`.

## Core

| Component | Module | Responsibility |
| --- | --- | --- |
| Tool contract | `tools/contracts.py` | `ToolSpec`, `@tool` |
| Registry | `tools/registry.py` | Explicit, unique registration and lookup |
| Permissions | `tools/permissions.py` | `Permission`, `PermissionPolicy`, allowlist, read-only default |
| Catalog and compatibility | `tools/catalog.py`, `tools/compatibility.py`, `tools/server_info.py` | Resources and prompts; schema changes against `scripts/tool-versions.lock`; `qaos://server/info` |
| Tool call | `runtime/call.py` | `ToolCall`, what every interface hands the executor |
| Executor | `runtime/executor.py` | Permissions, validation, one bounded pool, timeout, output size, `ToolCallRecord` |
| Approvals | `runtime/approvals.py` | Single-use approvals bound to run, tool, and input hash |
| Runner | `runtime/runner.py` | `run_command`: fixed directory, scrubbed environment, kills the process tree on timeout |
| Quotas and telemetry | `runtime/quotas.py`, `runtime/telemetry.py` | The `UsageGate` hook; optional OpenTelemetry spans |
| Evidence | `evidence/models.py` | `Evidence` (`retrieved`, `computed`, `interpretation`), `SourceRef` |
| Reporting | `reporting/` | `Report`, `RunMetadata`, Markdown rendering |
| Runs | `runs/` | `RunStore` (memory, SQLite), `RunRecorder`, redaction, append-only audit |
| Configuration | `config/settings.py` | The home, `QUANT_AGENT_OS_*` settings, `SecretStr` |
| Datasets | `data/` | Tables read by id with a fingerprint as their version; `list_datasets` |
| Tenancy and serving | `tenancy.py`, `serving.py` | Tenant ids; `Surface`, one tenant's executor, resources, prompts, and runs |
| MCP | `mcp/adapters.py`, `mcp/server.py`, `mcp/http.py` | Definitions and calls over the executor; stdio; stateless Streamable HTTP |
| REST | `api/` | FastAPI over the same executor; scoped API keys stored as hashes |
| SDK | `sdk/` | `Platform`: `call`, `report`, `approve`, `resume`, run lookup |
| Registration | `registration.py` | `FirmApp.expose`: a firm's typed function as a tool, with data by reference and provenance; `call_service`: a read-only internal service, from a typed stub |
| Contract checks | `testing.py` | `check_tool`, also used by generated tool packages; `check_app` and its schema lock |
| Observability | `observability/logging.py` | Logging with secret redaction |

## Layering and dependencies

Each product is layered the same way:

| Layer | Contains | Must not depend on |
| --- | --- | --- |
| Domain | Pydantic models, invariants, `Protocol` ports | Any framework, transport, database, or vendor |
| Application | Use-case services, such as `ResearchService` and `AppFactory` | Transports and concrete adapters |
| Infrastructure | Adapters: SQLite, PostgreSQL, embedders, pytemplate-uv | Interfaces |
| Interface | `tools.py` and `cli.py`, wired by `bootstrap.py` | Infrastructure internals |

Rules, enforced by `tests/contract/test_import_boundaries.py`:
- Core imports no product, and the products do not import each other.
- Only `quant_platform` imports every product, and nothing imports it.
- Only `quant_app_factory/infrastructure/pytemplate_uv.py` imports `pytemplate-uv`, and only
  `pytemplate.api` ([decision 0006](decisions/0006-pytemplate-uv-as-a-library.md)).
- `quant_analytics.metrics` imports numerical libraries only.
- Importing `quant_platform.cli` works with no extra installed, and serving imports no `cli` module.

Core imports downward only: `tools` → `runtime` → `evidence`, `reporting`, `runs` → `config`, `data`,
`serving`, `registration`, `mcp`, `sdk`, `testing`, `observability`, `cli`.

### From client to service

```text
MCP client ─┐
REST client ┼─▶ interface adapter ─▶ ToolExecutor ─▶ ToolSpec (@tool wrapper) ─▶ application service
SDK ────────┘     (no logic)        permissions · approval                              │
                                  validation · limits · record                          ▼
                                                                     domain models and ports
                                                                                        │
                                                                        infrastructure adapters
```

- An application service is an ordinary class with typed methods, tested without the platform.
- A `@tool` wrapper is a few lines that call one service method and return `warnings` and `evidence`.
- Every path goes through `ToolExecutor`, so permissions, approvals, validation, limits, and records
  apply identically. Contract tests assert that results match
  ([decision 0002](decisions/0002-mcp-boundary.md)).
- Trusted in-process code may import a service directly. That path produces no audit record.

## Tool contract and request lifecycle

A `ToolSpec` has a snake_case name and description; a `MAJOR.MINOR.PATCH` version; pydantic input and
output models; the plain function; its declared `Permission`s; `deterministic` and `idempotent` flags;
`timeout_seconds`; `max_output_bytes`; and an optional `Deprecation`. The `@tool` decorator builds one
from a typed function, which stays directly callable. Versions and schemas are checked against
`scripts/tool-versions.lock` ([tools changelog](tools-changelog.md)).

For each call:
1. **Choose.** The client or the caller's code decides what to call. The platform has no planner.
2. **Execute.** The executor checks permissions, validates the input, and takes one of its 16 slots
   (`busy` if none is free). It asks the usage gate to admit the call (`quota_exceeded`), checks for an
   approval when the tool is consequential (`approval_required`), and runs the tool with a timeout. It
   then validates the output and enforces its size limit. Failures become records, not exceptions.
3. **Report.** `research_report` and `Platform.report` collect evidence and warnings, turn failed calls
   into warnings, and add limitations and run metadata.
4. **Persist.** A `RunRecorder` writes the run, its records, and audit events as they happen.

The statuses are `ok`, `invalid_input`, `denied`, `approval_required`, `quota_exceeded`, `busy`,
`unknown_tool`, `timeout`, and `error`. No platform code reads tool output or retrieved text to choose
what runs next. A client's model reads them, so they stay marked untrusted.

## Quant Research

Measured results are in [Quant Research](products/quant-research.md#how-well-it-works): on real papers,
keyword search does most of the ranking, and the graph is small.

```text
 operator: qaos ingest DIR  (never an agent tool)
   ▼
 Loaders ─▶ Chunker ─▶ Rule-based extractors ─▶ Entity resolution ─▶ KnowledgeStore
 md, txt,   passages    vocabulary, front        canonical ids,      documents, chunks, entities,
 PDF text   with exact  matter, cue phrases      alias merge         mentions, relations, snapshots
            locations                                                      │
 MCP / SDK tool calls ─▶ ResearchService ─▶ retrieval ◀────────────────────┘
                                keyword · mention · graph · optional vector
                                     │ reciprocal rank fusion
                                     ▼
                               evidence ─▶ client's model, or a report
```

### Ingestion

- Ingestion writes to the store, so it is an operator command, which keeps agents read-only.
- Each document records a content `sha256`, a source URI, title, authors, date, a license note, and its
  `pipeline`: the loader, chunker, extractor, and vocabulary versions. Changed files, and unchanged files
  with a different pipeline, are re-processed.
- Each chunk keeps an exact location. Markdown and text have a line range and section, with paragraph
  chunks. PDF has a page and the line range of that page's extracted text, packed into chunks of about 500
  characters that close at sentence ends.
- PDF is the optional `pdf` extra (pypdf). Scanned, encrypted, or corrupt PDFs are skipped with a warning
  that says why.
- A **corpus snapshot** id is a hash over document hashes and extractor versions. Every answer records the
  snapshot it used.

### Extraction

The extractors are deterministic (`rules-v5`):
- a versioned vocabulary (`quant-v1`, bundled) of factors, signals, strategies, methods, metrics, asset
  classes, datasets, and concepts, with aliases. The longest alias wins, and short all-capital aliases match
  case-sensitively. `qaos ingest --vocabulary` merges a firm's terms, and the store keeps the result;
- document front matter;
- cue phrases between two entities in one sentence. "X predicts Y" yields `X predicts Y`, and passive and
  inverse forms swap subject and object. A negation in the three words before the cue suppresses the pair.
  Without a cue, the pair becomes `related_to` at confidence 0.4, which the default `min_confidence` of 0.5
  excludes.

Every mention and relation stores its extractor, version, confidence, and chunk. The predicates are
`predicts`, `correlates_with`, `explained_by`, `extends`, `contradicts`, `uses`, `tested_on`, and
`related_to`, in `quant_research.domain.models`. A change to them, the vocabulary, or the extractor bumps
the matching version, and so the corpus snapshot.

### Retrieval

1. **Seed.** Match vocabulary entities in the query. If it names none, seed from at most 3 entities in the
   best keyword passage, report them as direct results, and warn.
2. **Keyword channel.** BM25 over chunks, using the query's content terms: FTS5 on SQLite, and FTS5's BM25
   computed in SQL on PostgreSQL. A SQLite without FTS5 is refused with a clear message.
3. **Mention channel.** Chunks that mention the most seed entities.
4. **Graph channel.** A recursive CTE walks up to `hops` (default 1, maximum 2) from the seeds over edges
   with `confidence >= min_confidence`. A fact stated in many chunks returns at most 3 citing edges and a
   `support` count, and scores its confidence divided by hops, times `1 + log2(support)`. Each related entity
   carries the shortest chain of cited edges from a seed. Results are capped at 10 entities.
5. **Vector channel,** optional and off by default. Cosine similarity against chunk embeddings of the same
   model, keeping hits of at least the embedder's `min_score`: 0.8 for the measured local model, and 0.2 for
   hosted models, which have no measured floor.
   - `LocalEmbedder` uses fastembed and ONNX on this machine (`local-embeddings`).
   - `VoyageEmbedder` and `OpenAIEmbedder` call each provider's HTTPS endpoint with the standard library,
     with batching, retries, and backoff. They need the `network` permission.
   - The model id is part of the corpus snapshot. Searching with a model the store has no embeddings for warns.
6. **Fuse.** Reciprocal rank fusion (k = 60) over the channels, with ties broken by chunk id, capped at `k`.
7. **Package.** Passages with `SourceRef`, graph paths whose edges cite their chunks, matched entities, the
   snapshot id, and warnings.

Quant Research writes no answers: the client's model does. Retrieved documents are untrusted, and an
evaluation checks that injected instructions do not change tool selection or permissions.

### Storage

The relational schema is shared by both adapters. A graph database would be considered only if traversal
exceeded what recursive CTEs handle ([decision 0004](decisions/0004-knowledge-store.md)).

| Table | Key columns |
| --- | --- |
| `documents` | `id`, `sha256`, `uri`, `title`, `authors`, `published`, `license_note`, `loader_version`, `pipeline` |
| `chunks` | `id` (`<document>#<ordinal>`), `document_id`, `ordinal`, `text`, `line_start`, `line_end`, `section`, `page` |
| `chunks_fts` | FTS5 index of `chunks.text` (SQLite) |
| `entities` | `id` (`type:slug`), `type`, `name`, `aliases` |
| `mentions` | `entity_id`, `chunk_id`, `start`, `end`, `extractor`, `confidence` |
| `relations` | `id`, `subject_id`, `predicate`, `object_id`, `chunk_id`, `extractor`, `extractor_version`, `confidence` |
| `corpus_snapshots` | `seq`, `id`, `document_count`, `vocabulary_version`, `extractor_versions` |
| `embeddings` | `chunk_id`, `model`, `dimensions`, `vector` |

- Deleting a document cascades, so a changed file replaces all of its records.
- Ingestion publishes the snapshot in one transaction, and search reads run in one read transaction, so an
  answer never mixes two corpus versions.
- Serving opens the store read-only and never migrates it.
- `KnowledgeStore`, `VectorIndex`, `Embedder`, and `SourceLoader` are `Protocol`s. `SqliteKnowledgeStore`
  uses only the standard library, with exact brute-force cosine. `PostgresKnowledgeStore` (psycopg and
  pgvector, the `postgres` extra) passes the same contract tests and gives the same answers.

### Evaluation

`tests/research/research_eval.py` scores a gold set on fixtures: related-entity recall, passage precision,
citation and location validity, and empty-query accuracy. `graph_eval.py` scores extraction and paths against
a mock world with a known answer key. `real_corpus.py` scores 21 real papers and is opt-in, because `fetch`
needs a network. Tests fail on any drop below their baselines.

## Quant Analytics

`analyze_performance` and `analyze_risk` take dataset references, or small inline examples.
`quant_analytics.metrics` adapts empyrical-reloaded and states its conventions; `risk.py` computes volatility
contributions and factor exposures with numpy. Before a metric runs, the tools infer the frequency from the
dates, join series on them, and leave out and count missing rows. They refuse when frequency or alignment
cannot be established, and warn when returns look like percentages. Known-answer tests pin the conventions
([Quant Analytics](products/quant-analytics.md)).

## Quant App Factory

The factory turns a reviewable spec into a tested project. It is off by default on the server, and creating
files is a command the user runs. Generated quant projects stay portable, so they hold their own numpy metrics
and do not receive later fixes by themselves
([details](products/quant-app-factory.md#generated-metrics-and-the-platforms)).

```text
describe_template ──▶ person, through a client or `qaos app new` ──▶ spec.toml (the draft)
   ▲                                                                    │
   └──────────── check_spec: problems, or ready + exact files ◀─────────┘
                  (pytemplate.api.plan is the judge)
   │ ready
   ▼
qaos app create spec.toml ─▶ approval ─▶ ArchitecturePlanner ─▶ ScaffoldProvider
   ─▶ checks: uv sync · ruff · mypy · pytest (+ check-connection) ─▶ FactoryReport
                ▲
   factory.toml: identity, connections (environment-variable names), registered packs
```

- **`AppSpec`** names a `template` and a `profile`: `agent-tool`, `quant-baseline` with `quant-lab`, `bundled`
  with `python` or `fastapi`, or your own pack. The spec file is the draft, and there is no draft state.
- **`ArchitecturePlanner`** is deterministic and maps a spec to a scaffold request.
- **Template answers and runtime settings stay apart.** Answers come from the pack's questions and are used
  when rendering. Settings are read by the generated app, and secret ones accept only `env:NAME`.
- **Database guidance** is the `data-database` component. `create --check-connection` passes only the named
  variable, and only for built-in templates, so a secret never reaches pack code the operator did not review.
- **Approvals bind the reviewed pack.** Approval covers a digest of the pack's files, so a pack edited after
  its preview is refused.
- **Quant templates** ([decision 0009](decisions/0009-quant-baseline-templates.md)) carry known-answer metric
  tests, bias tests, and a `LIMITATIONS.md`. There is no trading code.
- **Your own templates.** `qaos app template fork` copies a profile into a pack, `template check` validates it,
  and `template add` registers a git pack fetched once at a tag, held to its commit and a digest of its files, and
  cached, so plans need no network ([decision 0025](decisions/0025-templates-are-starting-points.md)).

### The pytemplate-uv boundary

```python
class ScaffoldProvider(Protocol):
    name: str

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan: ...  # no writes
    # Called only inside an approved create_app call.
    def create(self, request: ScaffoldRequest) -> ScaffoldResult: ...
```

`PytemplateUvScaffolder` builds a `pytemplate.api.RenderRequest` and calls `api.plan`, which renders into a
temporary directory, or `api.render`, which publishes only on success. Nothing of pytemplate-uv is copied, and
its releases and plans are its own repository's business.

## Interfaces

| Interface | Audience | Built on |
| --- | --- | --- |
| MCP over stdio | Claude, Cursor, Copilot agents | `qaos serve`; tools, resources, and prompts |
| MCP over HTTP | Remote clients of a team server | `qaos serve --http`, stateless, at `/mcp` |
| REST | Web apps, non-Python clients | `qaos serve --http`, at `/v1` on the same port |
| Python SDK | Notebooks, scripts | `quant_platform.open_platform()`, or `quant_agent_os.sdk.Platform` |
| CLI | Operators | `qaos`; each product's `cli.py` is a Typer app it mounts |

Interfaces contain no business logic, and tool names and schemas are identical across them. MCP over stdio
runs as the local user with the operator's granted permissions, and HTTP needs an API key.

### Served tools

`qaos catalog` lists what a server offers. These tools are served, each with its permissions:

| Tool | Toolset | Permissions |
| --- | --- | --- |
| `find_related_signals` | research | `read_local_data`, plus `network` with a hosted embedder |
| `get_source_passage` | research | `read_local_data` |
| `research_report` | research and analytics | `read_local_data` |
| `analyze_performance`, `analyze_risk`, `list_datasets` | analytics | `read_local_data` |
| `describe_template`, `check_spec` | factory, off by default | none |
| `server_info` | always | none |

## Audit and provenance

- `ToolCallRecord` stores the tool, version, arguments, status, output or error, duration, `call_id`, `run_id`,
  `started_at`, `input_sha256`, and `data_versions`.
- `RunMetadata` stores the platform version, times, `run_id`, a `registry_digest` of tool names, versions, and
  schemas, and the merged data versions.
- Every MCP, REST, or SDK call is a run. Arguments and outputs are redacted before storage, and a store failure
  is logged and never fails the call.
- `audit_events` is append-only: triggers reject updates and deletes. Its details hold ids, hashes, and statuses,
  never inputs or secrets.
- `qaos serve` records to the home's `runs.db`, also used by factory runs. `qaos runs [RUN_ID]` lists runs or
  shows one.

## Security and approvals

See [security.md](security.md) for details.
- Read-only by default: only `read_local_data` is granted, with an explicit registry and an optional allowlist.
- `write_local_data`, `execute_code`, `network`, and `external_action` are consequential. Granting one makes a
  tool eligible to run, and each call still needs a single-use approval bound to the run, the tool, and the
  input hash ([decision 0005](decisions/0005-approval-gates.md)).
- Approvals come from the operator: through the SDK, a terminal prompt, or the approval page
  ([decision 0014](decisions/0014-approvals-in-the-client.md)). A model can never grant one. Standing approval
  (`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`) is empty by default, and each use is recorded.
- Programs run through `run_command`. The one exception to its scrubbed environment is App Factory's approved
  connection check, which passes exactly the variables a spec references and redacts their values.
- Specs, `factory.toml`, and generated files hold environment-variable names, never secret values, and a value
  that looks like a secret is refused.

## Deployment

| Mode | What runs |
| --- | --- |
| Library | `uv add` into a project; the SDK in a notebook or script |
| Local server | `qaos serve`, launched by the client over stdio |
| Team server | A Docker image running `qaos serve --http` over SQLite, or PostgreSQL with a read-only role ([deployment.md](deployment.md)) |

## Extending it

- **Models:** drive the tools with your vendor's tool loop over `tool_definitions` and `Platform.call`
  ([example](../examples/your-own-model/drive_tools.py)).
- **Tools:** register `ToolSpec`s from your own packages in an explicit `ToolRegistry`. There is no plugin
  discovery or dynamic code loading.
- **Ports:** `KnowledgeStore`, `VectorIndex`, `Embedder`, `SourceLoader`, `RunStore`, `DatasetStore`, and
  `ScaffoldProvider` are `Protocol`s with contract tests.

## Not in scope

Server-side planning or multi-agent orchestration; autonomous trading or external actions; unrestricted code
execution; a mandatory vector or graph database; broad connector coverage; and a plugin marketplace.
