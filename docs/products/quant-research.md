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
are reported, because pypdf has no OCR.

### Parsing PDFs with RAGFlow

If your firm runs [RAGFlow](https://github.com/infiniflow/ragflow) (1.0 or later), ingestion can
use its DeepDoc parser instead of pypdf. DeepDoc reads layout and tables and runs OCR on scanned
pages:

```bash
export RAGFLOW_URL=https://ragflow.internal     # plain http only for localhost
export RAGFLOW_API_KEY=...                      # or put both in the home's .env
uv run qaos ingest ~/research --pdf-parser ragflow
```

- Each PDF is uploaded to a temporary dataset, parsed, read back, and deleted. RAGFlow embeds
  the chunks while it parses, so its tenant needs an embedding model configured.
- Quant Research still chunks, links, and cites the text. A citation names the page and a line
  range within the page text RAGFlow returned, which can differ from pypdf's lines. Changing the
  parser re-processes the PDFs on the next ingestion.
- A table RAGFlow recognises becomes one passage, cited on the page where it starts, with one
  line per row and cells separated by `|`, so its numbers stay with their row and column
  headers. A table longer than 4,000 characters splits between rows. Table cells are searched
  but, as with pypdf, never read as relations.
- A chunk RAGFlow returns without a page position makes that file fail with a warning, rather
  than get a guessed citation.
- Only ingestion talks to RAGFlow; serving and search never do. `RAGFLOW_PARSE_TIMEOUT` (seconds,
  default 600) bounds each file's parse.
- Its quality on the real-paper evaluation has not been measured yet.

To draft a strategy specification whose every item cites its passages, run
`uv run qaos strategy "trend following"`. Add `--json` to save the spec. What the
corpus leaves undecided is listed as open questions, not filled with defaults.

`find_related_signals` can search part of the corpus: `source_prefix` keeps sources whose
location starts with it, such as `notes/`, and `published_from` and `published_to` keep sources
whose `published` front matter (a year, month, or day) overlaps the range. A source with no
readable date is left out of a date search, with a warning. The filter applies inside every
search channel and the graph walk, before the result limit, so passages and edges all come from
the chosen sources. It narrows a search and is not access control: anyone who can call the tool
can search the whole corpus ([decision 0021](../decisions/0021-per-caller-access.md)).

`get_source_passage` returns one passage in full. With `context` 1 or 2 it adds the passages
before and after it in the same document, each with its own citation, for a passage that starts
mid-argument.

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
- **PDFs.** With the default parser, text-layer PDFs only, with no OCR (see RAGFlow above). Some kerning artefacts ("ot her") remain, and tables are
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
