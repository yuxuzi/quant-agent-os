# quant-agent-os-research

**Quant Research** is the Graph RAG product of Quant-Agent-OS. It ingests your research notes and
papers and builds a knowledge graph of factors, signals, strategies, methods, and datasets in
which every edge cites the passage it came from. It then answers questions with evidence packets
that AI clients (Claude, Copilot, Cursor) turn into cited answers.

```bash
uv run quant-research ingest path/to/corpus          # operator command; writes the store
uv run quant-research search "What is related to short-term reversal?"
```

To register the read-only tools (`find_related_signals`, `get_source_passage`) in a Quant-Agent-OS
registry:

```python
from quant_agent_os.tools import ToolRegistry
from quant_research import open_research, research_tools

research = open_research(".quant-research/knowledge.db")
registry = ToolRegistry(research_tools(research.research))
```

The design is in [docs/architecture.md §6–7](../../docs/architecture.md). Storage is SQLite
(standard library) today, with PostgreSQL planned behind the same `KnowledgeStore` port.
