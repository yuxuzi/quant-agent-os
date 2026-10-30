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

Hosted embedders send chunk text (at ingestion) and query text (at search) to the provider.
Ingest and search with the same `--embedder` and `--embedding-model`. To compare a hosted
model with the baseline, run
`uv run python packages/quant-research/tests/research_eval.py --embedder voyage`. This live
run costs a few cents.

It supports Markdown and text with front matter. PDF needs the extra
`quant-agent-os-research[pdf]`, and its locations cite the page and line range. Scanned PDFs
are reported, because OCR is not supported.

To register the read-only tools (`find_related_signals`, `get_source_passage`) in a Quant-Agent-OS
registry:

```python
from quant_agent_os.tools import ToolRegistry
from quant_research import open_research, research_tools

research = open_research(".quant-research/knowledge.db")
registry = ToolRegistry(research_tools(research.research))
```

The design is in [docs/architecture.md §6–7](../../docs/architecture.md). Storage is SQLite
(standard library) today, including an exact vector index. PostgreSQL with pgvector is
planned behind the same `KnowledgeStore` and `VectorIndex` ports.
