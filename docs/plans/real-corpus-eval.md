# Plan: evaluate Quant Research on real papers

Scope: the step after the mock world ([graph-rag-mock-world.md](graph-rag-mock-world.md)).
Every earlier number came from text written alongside the rules that read it. This plan
measures the pipeline on real research papers, fixes what breaks first, and records a
baseline that later changes must not lower.

## Corpus

- **Papers:** 21 arXiv papers on factors, momentum, low volatility, earnings drift, order
  flow, and asset-pricing methods ([papers.toml](../../packages/quant-research/tests/real_corpus/papers.toml)).
- **Pinning:** each paper is pinned by version and sha256.
- **Not committed:** the PDFs are never committed or redistributed. arXiv's default licence lets
  arXiv distribute a paper, not this repository. Running `real_corpus.py fetch` downloads them
  once into `~/.cache/quant-agent-os/real-corpus`, and evaluation runs offline after that.
- **Questions:** 28 gold questions written from the abstracts
  ([gold.toml](../../packages/quant-research/tests/real_corpus/gold.toml)):
  - 19 topic questions in the papers' own terms;
  - 6 paraphrases that avoid those terms;
  - 3 questions the corpus cannot answer.
- **Labels:** each typed relation extracted from the corpus is judged by hand against the
  sentence that states it
  ([labels.toml](../../packages/quant-research/tests/real_corpus/labels.toml)). There are 57
  facts covering both extractor versions.

```bash
uv run python packages/quant-research/tests/real_corpus.py fetch      # once, needs network
uv run python packages/quant-research/tests/real_corpus.py eval       # offline
uv run python packages/quant-research/tests/real_corpus.py relations  # facts to label
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

## Known limitations (found here, not fixed)

- **Generic words in the vocabulary.** "Profitability" and "momentum" are also ordinary
  words, as in "the profitability of factors". One of the four wrong facts remaining is a
  generic "profitability" read as the factor. Fixing it needs context rules or LLM extraction
  (out of scope).
- **pypdf spacing artefacts.** Words split by kerning ("ot her", "e ffect") are not rejoined.
- **Most edges are `related_to`.** Real papers rarely state a relation in the cue phrases the
  rules know, so 95% of edges are `related_to`. The graph's value on real papers comes mainly
  from co-mention structure and citations, not typed facts.
- **Small labelled set.** 57 facts across 21 papers is a small sample. Precision of 0.75 has
  a wide confidence interval.
- **Not checked yet:** two-column layouts and multi-page tables were not examined separately.
