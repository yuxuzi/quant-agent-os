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

## Consequences

- The MVP adds no runtime dependency.
- Moving to PostgreSQL is an adapter swap, verified by the shared adapter contract tests.
- SQLite FTS5 availability depends on the Python build. The adapter detects a missing FTS5 and
  falls back to a simpler keyword scorer with a warning.
