# quant-agent-os-research

**Quant Research** is the Graph RAG product of Quant-Agent-OS. It ingests your research notes and
papers and builds a knowledge graph of factors, signals, strategies, methods, and datasets in
which every edge cites the passage it came from. It then answers questions with evidence packets
that AI clients (Claude, Copilot, Cursor) turn into cited answers.

```bash
uv run quant-research ingest path/to/corpus          # operator command; writes the store
uv run quant-research search "What is related to short-term reversal?"
uv run quant-research search "betting against beta" --embedder hashing   # offline mock vectors

export VOYAGE_API_KEY=...   # or OPENAI_API_KEY with --embedder openai
uv run quant-research ingest path/to/corpus --embedder voyage   # default model voyage-finance-2
uv run quant-research search "betting against beta" --embedder voyage
```

Serve the tools to Claude Code (or any MCP client) over stdio:

```bash
claude mcp add quant-research -- \
  uv run --directory /path/to/quant-agent-os quant-research serve-mcp --store /abs/path/kb.db
```

Or serve them over REST to callers with an API key (the `api` extra; see
[docs/security.md](../../docs/security.md#rest-interface)):

```bash
uv run quant-agent-os api-key new --principal me --scope tools:list --scope tools:call
uv run quant-research serve-api --store /abs/path/kb.db
```

Hosted embedders send chunk text (at ingestion) and query text (at search) to the provider.
Ingest and search with the same `--embedder` and `--embedding-model`. To compare a hosted
model with the baseline, run
`uv run python packages/quant-research/tests/research_eval.py --embedder voyage`. This live
run costs a few cents.

It supports Markdown and text with front matter. PDF needs the extra
`quant-agent-os-research[pdf]`, and its locations cite the page and line range. Scanned PDFs
are reported, because OCR is not supported.

To draft a strategy specification whose every item cites its passages, run
`uv run quant-research strategy "trend following"`. Add `--json` to save the spec. What the
corpus leaves undecided is listed as open questions, not filled with defaults.

To register the read-only tools (`find_related_signals`, `get_source_passage`,
`generate_strategy`) in a Quant-Agent-OS registry:

```python
from quant_agent_os.tools import ToolRegistry
from quant_research import open_research, research_tools

research = open_research(".quant-research/knowledge.db")
registry = ToolRegistry(research_tools(research.research))
```

The design is in [docs/architecture.md §6–7](../../docs/architecture.md).

### Storage: SQLite or PostgreSQL

Storage is SQLite (standard library) by default, including an exact vector index. For a team
server or a larger corpus, use PostgreSQL with pgvector (the `postgres` extra). It gives the
same answers, because it scores keywords with FTS5's BM25:

```bash
export PGPASSWORD=...          # or ~/.pgpass; never put the password in the URL
uv run quant-research ingest ~/research --store postgresql://qaos@db.internal/qaos
uv run quant-research serve-api --store postgresql://reader@db.internal/qaos
```

- The tables live in the `quant_research` schema; `open_research(..., schema=...)` picks
  another.
- pgvector is created when the role may create it. Otherwise the store works without the
  vector channel.
- Serving opens the store in read-only transactions. Give the server a role that can only
  `SELECT`, as `deploy/compose.postgres.yaml` does.
