# Plan: evaluate Quant Research on real papers

Scope: the step after the mock world ([graph-rag-mock-world.md](graph-rag-mock-world.md)).
Every earlier number came from text written alongside the rules that read it. This plan
measures the pipeline on real research papers, fixes what breaks first, and records a
baseline that later changes must not lower.

## Corpus

- **Papers:** 21 arXiv papers on factors, momentum, low volatility, earnings drift, order
  flow, and asset-pricing methods ([papers.toml](../../tests/research/real_corpus/papers.toml)).
- **Pinning:** each paper is pinned by version and sha256.
- **Not committed:** the PDFs are never committed or redistributed. arXiv's default licence lets
  arXiv distribute a paper, not this repository. Running `real_corpus.py fetch` downloads them
  once into `~/.cache/quant-agent-os/real-corpus`, and evaluation runs offline after that.
- **Questions:** 53 gold questions, most written from the abstracts
  ([gold.toml](../../tests/research/real_corpus/gold.toml)):
  - 19 topic questions in the papers' own terms;
  - 6 paraphrases that avoid those terms;
  - 28 questions the corpus cannot answer (3 at first, and 25 more added in C6).
- **Labels:** each typed relation extracted from the corpus is judged by hand against the
  sentence that states it
  ([labels.toml](../../tests/research/real_corpus/labels.toml)). There are 57
  facts covering both extractor versions.

```bash
uv run python tests/research/real_corpus.py fetch      # once, needs network
uv run python tests/research/real_corpus.py eval       # offline
uv run python tests/research/real_corpus.py ablation   # what each channel contributes
uv run python tests/research/real_corpus.py relations  # facts to label
```

This evaluation is opt-in, like the benchmark. Default CI checks only that the files agree with
each other and the unit tests for each fix below. `--update` records
`evaluation/real_baseline.json`.

## Steps and results (2026-10-30)

### C1. Harness and baseline

Measured with no code changes. Ingestion took 10 s for 2,245 chunks.

| Metric | Baseline | After C2–C4 |
| --- | --- | --- |
| Topic hit rate @5 / MRR | 1.0 / 0.925 | 1.0 / 0.899 |
| Paraphrase hit rate @5 | 0.667 | 0.833 |
| Unanswerable questions answered with nothing | **0.0** | **1.0** |
| Citation validity / location validity | 1.0 / 1.0 | 1.0 / 1.0 |
| Typed facts extracted | 56 | 16 |
| Labelled precision of typed facts | **0.30** (17 of 56) | **0.75** (12 of 16) |
| `related_to` share of all edges | 0.92 | 0.95 |

Keyword retrieval already works on real papers. Three problems needed fixing:
- **Wrong typed facts.** 70% of typed facts were wrong. They came from tables (correlation
  matrices became `correlates_with` edges), reference-list entries, and cues read across
  clauses: in "CRSP stocks and the BAB factor with transaction costs", the word "with"
  produced `dataset:crsp uses transaction-cost-model`.
- **Off-topic answers.** Every off-topic question returned five passages. "Protein folding
  prediction accuracy" matched passages on the words "prediction" and "accuracy".
- **Damaged words.** 1,005 words were hyphenated across lines ("momen-⏎tum") and 1,645
  ligatures ("ﬁ") broke vocabulary matching and keyword search.

### C2. PDF text normalisation (`pdf-v2`)

`normalize_pages` expands ligatures with NFKC and rejoins line-break hyphenation onto the first
line, so every page keeps its line count and locations stay exact. A hyphen stays when the same
document spells the word with a hyphen more often ("cross-sectional"), or when the first part
is a single letter ("t-statistic").

With C3, which landed in the same run, paraphrase hits rose from 0.667 to 1.0 before C4.

### C3. Extraction guards (`rules-v5`, chunker `page-v2`)

- **Prose only.** Sentences that are mostly numbers (at least 3 numeric tokens and over 20%
  of tokens), sentences over 80 words, and reference lists yield mentions but no edges. The
  reference list is the section after a "References" or "Bibliography" line, carried across
  pages until an appendix heading.
- **Type signatures.** Each typed predicate lists the entity types it can join. A dataset
  uses nothing, and an asset class or a metric is never tested on something. A pair whose cue
  matches but whose types do not becomes `related_to`. The extractor never retries a shorter
  cue, because that could reverse the edge ("forecast by" read as "forecast").
- **Distance.** At most 15 words may separate two mentions for a typed edge, and 25 for
  `related_to`. Beyond that, the pair gets no edge.

Precision rose from 0.30 to 0.75. The cost is 5 true facts lost: 17 true facts before, 12
after. They were stated across long sentences or in tables. The mock world's scores are
unchanged.

### C4. Abstention

If a query names no vocabulary entity, a keyword passage counts as evidence only when it
covers at least half of the query's IDF-weighted terms. A word the corpus never contains gets
the highest weight. If the query names an entity, nothing is filtered.

- **Measured gap:** on the real corpus, for answerable questions that name no entity,
  relevant passages cover 0.58–1.0 of their query. The exceptions are two paraphrases, whose
  best relevant passages cover 0.21 and nothing. The best passage for an off-topic question
  covers at most 0.49.
- **Result:** unanswerable questions now return nothing (0.0 → 1.0), and paraphrase hits
  settle at 0.833.
- **Lost paraphrase:** the artificial-market paraphrase lost its only, weak match.

Trade-off, recorded in `evaluation/baseline.json`:
- **Fixture paraphrase hit rate: 0.75 → 0.0.** Those four hits matched only 0.16–0.28 of
  their queries ("fell last month"). That is the same weak overlap that answered off-topic
  questions. Paraphrase recall without a semantic embedder now has to come from a real
  embedding model, as decision 0004 already requires.
- **Fixture passage precision:** rose from 0.758 to 0.771.

## C5. Channel ablation (2026-11-05)

Simplification plan step P4.1, reproduced by `real_corpus.py ablation`. There are 19 topic
questions, 6 paraphrases, and 28 off-topic questions, so one paraphrase moves its hit rate by
0.167.

| Setting | Topic hit @5 | Topic MRR | Paraphrase hit @5 | Paraphrase MRR | Abstention |
| --- | --- | --- | --- | --- | --- |
| **Default** (keyword, mention, graph) | 1.0 | 0.899 | 0.833 | 0.750 | 1.0 |
| Without graph | 1.0 | 0.947 | 0.667 | 0.667 | 1.0 |
| Without mention | 1.0 | 0.895 | 0.833 | 0.750 | 1.0 |
| Without keyword | 0.737 | 0.462 | 0.500 | 0.367 | 1.0 |
| Keyword only | 1.0 | 1.0 | 0.667 | 0.667 | 1.0 |
| Mention only | 0.684 | 0.546 | 0.333 | 0.200 | 1.0 |
| Graph only | 0.053 | 0.053 | 0.333 | 0.250 | 1.0 |
| Default + `related_to` edges (`min_confidence=0`) | 1.0 | 0.866 | 0.667 | 0.583 | 1.0 |
| Default + mock hashing vector | 1.0 | 0.897 | 0.833 | 0.667 | **0.0** |

What it shows:
- **Keyword search does the ranking.** It alone puts a relevant paper first on all 19 topic
  questions. Adding the other channels lowers topic MRR from 1.0 to 0.899.
- **The graph channel is a small trade.** It rescues one paraphrase ("Which strategies earn
  returns without crash risk?") and pushes two topic questions down, from rank 1 to 3 and from
  rank 2 to 4.
- **The mention channel barely matters beside the others.** Removing it moves six ranks, three
  each way, and leaves every aggregate within 0.005.
- **The graph that retrieval walks is 19 edges.** The corpus has 357 edges, of which 338 are
  `related_to` at confidence 0.4. The default `min_confidence` of 0.5 skips those. Across the
  53 questions, a client sees 7 distinct edges, all typed. Including `related_to` edges lowers both
  MRRs and loses a paraphrase, so the filter stays.
- **The extracted facts are few.** 16 distinct typed facts in 21 papers: `tested_on` 7, `uses`
  5, `explained_by` 2, `correlates_with` 1, `predicts` 1. 12 of them are correct.
- **The vector channel skips abstention.** With the mock embedder, every off-topic question
  returns passages, because the coverage check applies only to keyword hits. A real embedder
  has to pass this measure as well as beat paraphrase 0.833 (P4.3).

Limits of this measure:
- `channels` limits only how passages are fused. With "keyword" off, the keyword index still
  chooses fallback seeds and still decides abstention, so "without keyword" and "graph only"
  flatter those settings.
- It scores passage ranking at document level. It says nothing about `related` entities and
  cited paths, which are what the graph adds for a client, and the gold set has no multi-hop
  question. In the mock world, multi-hop evidence recall is 1.0 with or without the graph
  channel, and evidence precision rises from 0.854 to 0.896.
- The topic questions use the papers' own terms, which favours keyword search.

No retrieval default changed here. The sample is too small to justify one, and the plan uses
this step to decide how the product is described.

## C6. A local embedding model (2026-11-05)

Simplification plan step P4.3: `--embedder local`, in the `local-embeddings` extra, runs
`BAAI/bge-small-en-v1.5` through fastembed and ONNX on the machine. `real_corpus.py eval --local`
reproduces this.

| Metric | Default | Local model, floor 0.2 | Local model, floor 0.8 |
| --- | --- | --- | --- |
| Topic hit rate @5 / MRR | 1.0 / 0.899 | 1.0 / 1.0 | 1.0 / 1.0 |
| Paraphrase hit rate @5 / MRR | 0.833 / 0.750 | 1.0 / 0.917 | 1.0 / 0.917 |
| Off-topic questions answered with nothing (first 3) | 1.0 | **0.0** | 1.0 |
| Citation validity / location validity | 1.0 / 1.0 | not recorded | 1.0 / 1.0 |
| Ingestion, 2,264 chunks | about 10 s | 144 s | 168 s |

**The floor.** The vector channel does not pass through the keyword coverage check, so at the old
floor of 0.2 every off-topic question returned passages. Each `Embedder` now carries its own
`min_score`, and `LocalEmbedder` offers only models with a measured one.

- **How 0.8 was chosen.** On the first 3 off-topic questions the top score reached 0.764, and
  the one paraphrase that relies on the vector channel scored 0.802. Floors of 0.78 and 0.80 lose
  nothing; 0.82 loses that paraphrase (0.833) and lowers topic MRR to 0.974.
- **How it was checked.** 25 more off-topic questions, 10 far from finance and 15 close to it
  (mortgages, catastrophe bonds, audit fees), were written before any of their scores were seen.
  All 25 returned nothing. The highest top score among them was 0.731.

What this does not show:
- **One model on one corpus.** The floor is not transferable: a different model, or a much larger
  corpus, needs measuring again.
- **The margin is narrow on the answering side.** 0.802 against a floor of 0.8 is one paraphrase
  passing by 0.002. Six paraphrases can't show how many a firm's questions would lose. In one
  informal check on a one-paragraph note, a loosely worded on-topic question returned nothing.
- **Hosted models have no measured floor.** They keep 0.2, so off-topic questions can return
  passages with them.
- **The model is not pinned to a file hash.** fastembed downloads it by name from Hugging Face on
  first use.

It stays off by default, as ingestion is about 15 times slower and the extra adds 28 packages.

## Known limitations (found here, not fixed)

- **Generic words in the vocabulary.** "Profitability" and "momentum" are also ordinary
  words, as in "the profitability of factors". One of the four wrong facts remaining is a
  generic "profitability" read as the factor. Fixing it needs context rules or LLM extraction
  (out of scope).
- **pypdf spacing artefacts.** Words split by kerning ("ot her", "e ffect") are not rejoined.
- **Most edges are `related_to`.** Real papers rarely state a relation in the cue phrases the
  rules know, so 95% of edges are `related_to`, and default retrieval does not walk them (see C5).
- **Small labelled set.** 57 facts across 21 papers is a small sample. Precision of 0.75 has
  a wide confidence interval.
- **Not checked yet:** two-column layouts and multi-page tables were not examined separately.
