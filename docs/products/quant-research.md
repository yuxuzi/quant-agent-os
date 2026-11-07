# Quant Research

**Quant Research** is cited keyword search with entity linking and a small graph of extracted facts,
plus optional semantic search. It ingests your research notes and papers, finds the factors, signals,
strategies, methods, and datasets they mention, and extracts the relations they state. Every passage
and every fact cites where it came from. It returns evidence that AI clients (Claude, Copilot, Cursor)
turn into cited answers, and it writes no answers itself. On a firm's server it is one toolset beside the firm's own tools.

```bash
uv run qaos ingest path/to/corpus          # operator command; writes the store
uv run qaos search "What is related to short-term reversal?"
uv run --extra local-embeddings qaos search "betting against beta" --embedder local  # on this machine

export VOYAGE_API_KEY=...   # or OPENAI_API_KEY with --embedder openai
uv run qaos ingest path/to/corpus --embedder voyage   # default model voyage-finance-2
uv run qaos search "betting against beta" --embedder voyage
```

Serve the tools to Claude Code (or any MCP client) over stdio:

```bash
claude mcp add quant-research -- \
  uv run --directory /path/to/quant-agent-os qaos serve --toolsets research --store /abs/path/kb.db
```

Or serve them over REST to callers with an API key (the `api` extra; see
[docs/security.md](../security.md#rest-interface)):

```bash
uv run qaos keys new --principal me --scope tools:list --scope tools:call
uv run qaos serve --http --toolsets research --store /abs/path/kb.db
```

Hosted embedders send chunk text (at ingestion) and query text (at search) to the provider.
Ingest and search with the same `--embedder` and `--embedding-model`. To compare a hosted
model with the baseline, run
`uv run python tests/research/research_eval.py --embedder voyage`. This live
run costs a few cents.

It supports Markdown and text with front matter. PDF needs the extra
`quant-agent-os[pdf]`, and its locations cite the page and line range. Scanned PDFs
are reported, because OCR is not supported.

To draft a strategy specification whose every item cites its passages, run
`uv run qaos strategy "trend following"`. Add `--json` to save the spec. What the
corpus leaves undecided is listed as open questions, not filled with defaults.

To register the read-only tools (`find_related_signals`, `get_source_passage`,
`generate_strategy`) in a Quant-Agent-OS registry:

```python
from quant_agent_os.tools import ToolRegistry
from quant_research import open_research, research_tools

research = open_research("knowledge.db")
registry = ToolRegistry(research_tools(research.research))
```

The design is in [architecture.md](../architecture.md#quant-research).

## How well it works

Measured on 21 real arXiv papers with 53 gold questions
([full results](../plans/real-corpus-eval.md), sections C5 and C6). Reproduce them with
`uv run python tests/research/real_corpus.py fetch`, which downloads the papers once, then
`... real_corpus.py eval`.

| Question | Default | With `--embedder local` |
| --- | --- | --- |
| Topic questions with a relevant paper in the top 5 | 19 of 19 | 19 of 19 |
| Topic ranking (MRR; keyword search alone scores 1.0) | 0.899 | 1.0 |
| Paraphrased questions with a relevant paper in the top 5 | 5 of 6 | 6 of 6 |
| Off-topic questions correctly answered with nothing | 28 of 28 | 28 of 28 |

**Keyword search does most of the work.** The graph is small: these papers yield 357 edges, of which
338 are unverified co-mentions that the default confidence filter skips, leaving 19 edges and 16
distinct typed facts. 12 of those 16 are correct. Removing the graph from ranking gains topic MRR
(0.947) and loses one paraphrase. What the graph adds for a client is related entities with cited
paths, which these figures do not score. The gold set is small and has no multi-hop question.

## Known limitations

- **Vocabulary.** Entity and graph retrieval know the 36 bundled entities and any you add with
  `--vocabulary`. Anything else is found only by keyword search.
- **Generic words.** Words that are also everyday English ("profitability", "momentum") can be read
  as the factor.
- **Few typed relations.** Typed relations need explicit phrasing such as "predicts", "explained by",
  or "tested on". Most co-occurrences stay `related_to`.
- **Paraphrases.** Without an embedder, a question that shares no words with the answer can miss.
- **Hosted embedders have no measured score floor,** so off-topic questions can return passages with
  them. The local model's floor of 0.8 was measured on one corpus and may not transfer.
- **PDFs.** Text-layer PDFs only, with no OCR. Some kerning artefacts ("ot her") remain, and tables are
  indexed for search but never read as relations.
- **Answers.** The client writes the answer from the evidence, so check its citations against the
  passages it quotes.

### Storage: SQLite or PostgreSQL

Storage is SQLite (standard library) by default, including an exact vector index. For a team
server or a larger corpus, use PostgreSQL with pgvector (the `postgres` extra). It gives the
same answers, because it scores keywords with FTS5's BM25:

```bash
export PGPASSWORD=...          # or ~/.pgpass; never put the password in the URL
uv run qaos ingest ~/research --store postgresql://qaos@db.internal/qaos
uv run qaos serve --http --toolsets research --store postgresql://reader@db.internal/qaos
```

- The tables live in the `quant_research` schema; `open_research(..., schema=...)` picks
  another.
- pgvector is created when the role may create it. Otherwise the store works without the
  vector channel.
- Serving opens the store in read-only transactions. Give the server a role that can only
  `SELECT`, as `deploy/compose.postgres.yaml` does.
