# Plan: Quant Research retrieval service

Scope: backlog items B-05 and B-06 in [ENGINEERING_REVIEW_PLAN.md](../ENGINEERING_REVIEW_PLAN.md).
The design is in [architecture.md §6–7](../architecture.md). The service is
`quant_research.application.retrieval.ResearchService`, exposed through the
`find_related_signals` and `get_source_passage` tools.

## Starting point (commit `1f94aa5`, 2026-10-30)

These pieces exist and work end to end on the fixture corpus (6 documents, 8 chunks, 32
relations):
- ingestion, which is idempotent and keeps a stable snapshot id;
- the SQLite store, with FTS5 keyword search and recursive-CTE graph expansion;
- reciprocal rank fusion;
- evidence packets;
- the CLI.

There are no unit tests for the package yet.

Probing five queries gave these findings:

| # | Finding | Evidence |
| --- | --- | --- |
| F1 | Stopwords reach keyword search, so noise passages rank (including the injection fixture) | "What is related to short-term reversal?" returns `notes/injection.md` and `value-and-quality.md` |
| F2 | The keyword-seeded fallback is far too broad | "market makers spread" seeds 14 entities, and a relation from the injection note leaks in |
| F3 | Chunks are coarse, so citations are imprecise | `momentum-crashes.md` is one chunk, lines 10–20 |
| F4 | A query with no results returns no warning | "crypto funding rates" |
| F5 | Passages that define a seed entity rank below graph-only passages | The reversal summary (lines 12–13) ranks 4th |

## Steps

Each step ends with all four quality gates passing and a commit.

| Step | Status |
| --- | --- |
| R1 Characterization tests | Done (`9f5be8f`) |
| R2 Evaluation harness | Done (`b58acbd`). Baseline: recall 0.75, precision 0.5972 |
| R3 Quality fixes | Done (`b58acbd`). Recall 1.0, precision 0.9167, validity 1.0 |
| R4 Injection resistance | Done |
| R5 Tools, MCP, reporting | Done. It found and fixed a defect: SQLite connections were bound to one thread, so every executor and MCP call failed |
| R6 Documentation sync | Done |

### R1. Characterization tests (no behaviour change)

Tests:
- The matcher prefers the longest match, matches short capitals case-sensitively, and accepts
  plurals and hyphens.
- Chunk line ranges equal the source file's lines, including after front matter.
- Each extraction cue produces its predicate, and the same sentence never yields self-relations.
- The store:
  - re-ingesting is idempotent and keeps the snapshot;
  - a changed file replaces its old records;
  - the keyword fallback works without FTS5;
  - `expand` respects `hops` and `min_confidence`;
  - CTE cycles terminate.

Accept when the package has unit tests for every module except the CLI, and the CLI has a smoke
test.

### R2. Evaluation harness and recorded baseline

Measure before improving. Add `tests/evaluation/gold.toml` (queries, expected related entities,
and the documents expected to be cited) and an evaluation module that computes:
- **related recall**: expected entities found among `related`;
- **passage precision@k**: passages that come from the expected documents;
- **citation validity**: each graph link's chunk mentions both of its entities. This must be
  100%.
- **location validity**: each passage's line range reproduces its text from the source file.
  This must be 100%.

The baseline is recorded in `baseline.json`. The test fails if a metric drops below the baseline,
and the baseline is updated only on purpose.

### R3. Retrieval quality fixes, each measured against R2

1. Add a stopword filter and a minimum token length for the keyword channel (F1).
2. Chunker `paragraph-v2`: one paragraph per chunk, merging only paragraphs under 200 characters
   (F3). The chunker version is part of the snapshot, so snapshots change as intended.
3. Add a mention channel: chunks that mention the seed entities become a third RRF ranking (F5).
4. Tighten the fallback: seed only from the best keyword chunk, take at most 3 entities, and
   warn (F2).
5. Warn when the query matches no passage and no entity (F4).

Accept when precision@k improves on the baseline, recall does not drop, and validity stays at
100%. Then update the baseline.

### R4. Injection resistance

Tests:
- The injection fixture is stored and returned only as `retrieved` passage text.
- Tool descriptors and permissions are unchanged after ingesting it.
- An orchestrated run whose tool output contains injected text sends the provider exactly one
  planning prompt, and that prompt does not contain the text. This checks the
  no-feedback-into-planning invariant from the research side.

### R5. Tool interface, MCP, and reporting

Tests and example:
- Contract tests for the research tools: complete descriptors, `warnings`/`evidence` outputs,
  MCP results equal to direct calls, and output within `max_output_bytes`.
- An unknown chunk id records an `error` status with an actionable message.
- An orchestrated research run (fake provider) produces a report whose metadata records the
  `corpus_snapshot` data version.
- `examples/quant-research/` ingests the fixture corpus into a temporary store and calls the
  tools through the MCP adapter.

### R6. Documentation sync

- Update architecture §6–7: the predicate list, the `quant-research` CLI, and `:memory:` SQLite
  instead of a separate in-memory store.
- Add a note to decision 0004.
- Mark B-05 and B-06 as done in the plan.
- Add a changelog entry.

## Deferred (not in this plan)

Vector retrieval and an embedding provider (B-14), the PostgreSQL adapter (B-14), the LLM
extractor, the PDF loader, server-side synthesis (`research_alpha`, B-15), and the MCP server
transport (B-01, waiting for approval of the `mcp` dependency).
