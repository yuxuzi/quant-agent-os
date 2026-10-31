# 0004: A relational knowledge graph behind a `KnowledgeStore` port

- Status: proposed
- Date: 2026-10-30
- Updates: AD-6 (local documents without a mandatory vector database)

## Context

Quant Research needs:
- documents and chunks with exact locations;
- typed entities and relations, each citing its supporting chunk;
- keyword and graph retrieval, with vector retrieval later.

The baseline recommends starting with PostgreSQL and adopting a graph database only when the
requirements justify one. Two project invariants also apply: tests run offline, and the
platform requires no infrastructure the user does not already own.

## Decision

- Use one relational schema (documents, chunks, entities, mentions, relations, corpus
  snapshots, and embeddings later). Graph expansion uses recursive CTEs, which run on both SQLite
  and PostgreSQL.
- Define `KnowledgeStore` as a `Protocol` with three adapters:
  - in-memory, for unit tests;
  - SQLite (standard library, FTS5), for the MVP, the demo, and offline CI;
  - PostgreSQL with pgvector, as an optional extra, at days 31–60.
- Add vector retrieval only after the citation evaluation shows a gain over keyword plus graph
  retrieval.
- Re-evaluate a dedicated graph database only when measured queries exceed two hops, the scale
  exceeds what PostgreSQL handles comfortably, or a query pattern cannot be expressed with CTEs.

## Implementation note (2026-10-30)

The SQLite adapter covers unit tests through `":memory:"`, so no separate in-memory adapter
exists. The schema, the FTS5 fallback, and recursive-CTE expansion are implemented in
`quant_research.infrastructure.sqlite_store`. The connection is shared across threads under a
lock, because `ToolExecutor` runs tools on worker threads.

### Vector channel (2026-10-30)

`SqliteKnowledgeStore` also implements the `VectorIndex` port: float32 blobs and exact cosine
search. `HashingEmbedder` is an offline mock embedder.
- With the mock, the evaluation measured equal metrics with and without vectors. Following
  this decision, the channel is therefore opt-in (`vector=True`).
- A test keeps it from ever lowering a metric.
- A real embedder becomes the default only when it beats the baseline.

Hosted models (added 2026-10-30): `VoyageEmbedder` (default `voyage-finance-2`) and
`OpenAIEmbedder` implement the same port over HTTPS without new dependencies. The same rule
applies to them. They are opt-in until the live evaluation, which includes paraphrase queries,
shows a gain.

Graph expansion at scale (2026-10-30): the mock-world benchmark showed two-hop expansion
growing with the number of chunks stating the same fact, at 1.1 s p50 for 3,000 documents. An
entity-level walk, parallel-edge aggregation (at most 3 citing edges plus a support count),
and splitting the recursive CTE from the edge query (SQLite re-ran the inlined CTE per joined
row) brought it to 14 ms. The relational design still holds at this scale, so a graph database
is not yet justified.

DuckDB was evaluated for this store and not chosen. Its full-text and HNSW extensions download
on first use, which breaks offline tests, and the workload is point lookups and traversal, not
columnar scans. DuckDB remains the candidate for the analytics and backtest service.

## Consequences

- The MVP adds no runtime dependency.
- Moving to PostgreSQL is an adapter swap, verified by the shared adapter contract tests.
- SQLite FTS5 availability depends on the Python build. The adapter detects a missing FTS5
  (`has_fts` is false) and falls back to a simpler term-overlap scorer.
