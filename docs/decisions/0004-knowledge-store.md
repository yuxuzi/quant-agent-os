# 0004: A relational knowledge graph behind a `KnowledgeStore` port

- Status: accepted (SQLite in B-05, PostgreSQL in B-14)
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

### PostgreSQL adapter (2026-11-02, B-14)

`PostgresKnowledgeStore` (the `postgres` extra, psycopg 3) implements both ports over the same
relational design, in its own schema (`quant_research` by default). It gives SQLite's answers,
not merely similar ones. The shared contract tests check this, on every case, against an
SQLite reference: BM25 scores, evidence packets, and strategy specs.
- **Keyword ranking.** PostgreSQL's `ts_rank` is not BM25, so it is not used. Terms are
  indexed with an FTS5-compatible tokenizer (`infrastructure/terms.py`) in a `chunk_terms`
  table, and FTS5's BM25 is computed in SQL (k1 = 1.2, b = 0.75, negative IDF clamped).
- **Ordering.** Identifier columns use the `C` collation, so ties break by bytes, as in SQLite.
- **Graph walk.** PostgreSQL allows one recursive reference, so the walk reads both directions
  of each relation from one non-recursive edge list.
- **pgvector is optional.** The store creates it in `public` when allowed. Without it, the
  vector channel reports unavailable and everything else works. Search is exact cosine; an
  HNSW index would return approximate results, so it waits until the evaluation needs the
  scale.
- **Concurrency.** Reads hold one repeatable-read, read-only transaction. Publishing a snapshot
  takes a lock that conflicts with document writes, as SQLite's `BEGIN IMMEDIATE` does.
- **Credentials.** Passwords come from `PGPASSWORD` or `.pgpass`, never from logged URLs.
  Messages redact any password a URL carries.

No SQLAlchemy: plain psycopg keeps one SQL dialect per adapter, written to match the other.

DuckDB was evaluated for this store and not chosen. Its full-text and HNSW extensions download
on first use, which breaks offline tests, and the workload is point lookups and traversal, not
columnar scans. DuckDB remains the candidate for the analytics and backtest service.

## Consequences

- The MVP adds no runtime dependency.
- Moving to PostgreSQL is an adapter swap (`--store postgresql://…`), verified by the shared
  contract tests (`test_research_store_contract.py`), which CI runs against pgvector.
- SQLite FTS5 availability depends on the Python build. The adapter detects a missing FTS5
  (`has_fts` is false) and falls back to a simpler term-overlap scorer.
