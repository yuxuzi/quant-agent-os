# Plan: build and test Graph RAG on a mock world with ground truth

Scope: the graph half of Quant Research. The retrieval plan
([quant-research-retrieval.md](quant-research-retrieval.md)) proved that the pipeline runs on
six hand-written documents. It cannot tell whether the **graph** is right, because nothing
records which relations the documents actually state. This plan adds mock data with a known
answer key, measures the graph against it, and fixes what the measurements show.

## Starting point (`68699f4`, 2026-10-30)

These pieces exist:
- rule-based extraction (vocabulary matching plus cue phrases, same sentence, pairwise);
- a recursive-CTE expansion of at most 2 hops;
- related entities scored by the sum of confidence divided by hops;
- four rank-fusion channels.

Code inspection found these gaps, and G2 is expected to confirm them:

| # | Suspected gap | Why |
| --- | --- | --- |
| K1 | Negation is extracted as a positive relation | "X does not predict Y" contains "predict" |
| K2 | Passive voice reverses the direction | The subject is always the first mention, so "Y is predicted by X" yields `Y predicts X` |
| K3 | Active "explains" is missed | The cue only matches "explained by", so "X explains Y" becomes `related_to` |
| K4 | Two-hop results do not show how they connect | `links` holds only the edges touching the entity, not the path from the seed |
| K5 | The graph's value for retrieval is unmeasured | No evaluation without the graph channel |

## Results (2026-10-30)

| Metric on the mock world | Baseline (G2) | After G3–G5 |
| --- | --- | --- |
| Extraction precision / recall | 0.8125 / 0.9286 | 1.0 / 1.0 |
| Negation false positives | 4 | 0 |
| Direction errors | 2 | 0 |
| Path validity / expected path accuracy | 0 / 0 | 1.0 / 1.0 |
| Evidence precision@3, with graph / without | 0.875 / 0.8542 | 0.8958 / 0.8542 |
| Related recall, evidence recall, unanswerable | 1.0 | 1.0 |

Caveats:
- I wrote the world and the rules together, so 1.0 on extraction shows that the rules handle
  these phrasings, not that they generalise. Thirteen held-out phrasings are unit-tested, and
  a larger held-out or real corpus is the next real check.
- The graph channel's measured retrieval gain is small (+0.04 precision@3) on a six-document
  world. Its main value here is the paths and typed relations in answers.

Scale (G5, `research_bench.py`, this Mac, no vectors):

| World size | Ingest | 1-hop p50 / p95 | 2-hop p50 / p95 |
| --- | --- | --- | --- |
| 300 documents, 1,500 relations | 0.4 s | 0.8 / 2.1 ms | 1.6 / 4.1 ms |
| 3,000 documents, 15,000 relations, before the fix | 4.8 s | 9.5 / 22.4 ms | 1,073 / 3,330 ms |
| 3,000 documents, 15,000 relations, after the fix | 4.8 s | 5.8 / 18.0 ms | 14.3 / 37.2 ms |

The benchmark found that two-hop expansion grew with the number of chunks stating the same
fact. Two changes fixed it:
- The walk now runs over entities, with parallel edges aggregated: at most 3 citing edges per
  fact, plus a `support` count.
- The expansion now runs as two queries, because SQLite re-ran an inlined recursive CTE per
  joined row.

Scoring counts each fact once, weighted by `1 + log2(support)`. The hashing vector channel
adds about 85 ms at 6,500 chunks (brute-force cosine), which is where pgvector takes over.

## Steps

Each step ends with all quality gates passing and a commit.

### G1. Mock world generator (test support, no committed binaries)

`tests/mock_world.py` declares a synthetic world: about 30 true relations over vocabulary
entities, spread over 6 documents (Markdown, text, and 2 PDFs). Each document renders
deterministically, with known phrasings that cover:
- active and passive voice;
- aliases (HML, QMJ, PEAD, BAB);
- negations;
- "explains";
- distractor co-mentions;
- filler sentences that contain no entity.

The generator returns the **answer key**:
- the true relations, each with its document;
- the negated pairs, which must not be extracted;
- gold questions: one-hop, two-hop with the expected path, alias, PDF, and unanswerable.

Accept when the generator is deterministic and every true relation's sentence names both
entities in vocabulary surface forms (a test checks both).

### G2. Graph evaluation and recorded baseline

`tests/graph_eval.py` measures the following:
- **extraction**:
  - precision, recall, and F1 of typed relations (excluding `related_to`) against the key,
    per predicate and overall;
  - **negation false positives**;
  - **direction errors**;
- **answers**:
  - related-entity recall per question;
  - **path validity**: the reported path starts at a seed, is connected, and ends at the
    entity;
  - **evidence recall@k**: the documents stating the expected relations appear in the top-k
    passages;
- **graph ablation**: evidence recall with and without the graph channel.

The baseline is recorded in `graph_baseline.json`. Tests fail on regression.

### G3. Extraction fixes, measured

Each fix targets one gap and must not lower any metric:
1. K1: negation cues ("not", "no", "does not", "fails to", "never") between two entities
   suppress the typed relation and add no edge.
2. K2: passive cues ("is predicted by", "is used by", and similar) swap subject and object.
3. K3: active "explains" or "captures" produces `explained_by` with the subject and object
   swapped.

The extractor version bumps to `rules-v3`, so snapshots change as intended.

### G4. Graph paths in answers

`RelatedEntity.path` lists the edges from a seed to the entity: the shortest path, with ties
broken by higher confidence and then by id. Evidence covers every path edge, the CLI shows the
path, and path validity must be 1.0.

### G5. Ablation and scale check

- Record the graph ablation from G2 in the plan. The graph channel should raise evidence
  recall on multi-hop questions; if it does not, say so.
- Add an opt-in benchmark (`research_bench.py`) that generates the world at about 50 times
  the size (around 300 documents) and reports ingestion time and query latency at p50 and
  p95. It is not a test, because timings vary between machines.

## Out of scope

LLM extraction, entity disambiguation beyond the vocabulary, and a graph database (decision
0004). Each is reconsidered only when these measurements show a ceiling.
