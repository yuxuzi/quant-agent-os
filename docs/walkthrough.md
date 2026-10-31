# Walkthrough: cited research answers in Claude

This walkthrough goes from a clean checkout to Claude answering research questions from your
own documents. Every answer comes with file, page, and line citations, and every call is
recorded. It takes about ten minutes. Every command was run on a fresh clone with
`uv sync --locked` on 2026-10-30. The outputs shown are from that run, with table borders
removed and long ids shortened.

You need [uv](https://docs.astral.sh/uv/) and, for steps 4–5, Claude Code or Claude Desktop.
Nothing here needs an API key or sends data off your machine.

## 1. Install

```bash
git clone https://github.com/yuxuzi/quant-agent-os.git
cd quant-agent-os
uv sync --locked
```

## 2. Build a knowledge store

Start with the small sample corpus: six short notes on reversal, momentum, order flow, value,
and earnings drift, one of them a prompt-injection test. Step 7 uses your own documents.

```bash
uv run quant-research ingest packages/quant-research/tests/fixtures/corpus --store ~/qr-demo/kb.db
```

Ingestion runs offline:
- **What it does:** it chunks each document, finds the factors and signals it mentions,
  extracts the relations between them, and records a corpus snapshot id.
- **Re-running:** running it again re-reads only changed files.
- **Formats:** `.md`, `.txt`, and `.pdf` are read. Other files are skipped with a warning
  (here `chart.png`).

## 3. Ask from the terminal

```bash
uv run quant-research search "What is related to short-term reversal?" --store ~/qr-demo/kb.db
```

```text
 Entity               Type    Score  Connection
 Liquidity provision  signal  0.80   signal:short-term-reversal -correlates_with-> signal:liquidity-provision
 Order-flow imbalance signal  0.80   signal:short-term-reversal <-predicts- signal:order-flow-imbalance
 Momentum             factor  0.70   signal:short-term-reversal <-contradicts- factor:momentum
reversal-and-microstructure.md (lines 17-19, § Mechanism) keyword, mention, graph
notes/order-flow.md (lines 3-6, § Order-flow imbalance notes) keyword, mention, graph
momentum-crashes.md (lines 10-14, § Momentum, reversal, and crashes) keyword, mention, graph
...
Corpus snapshot snap:8cddf9d734e07c1c.
```

Each related entity shows the graph path that connects it to your question, and each passage
shows where it came from and which retrieval channels found it.

## 4. Connect Claude Code

Use absolute paths, because the client starts the server from its own working directory.

```bash
claude mcp add quant-research -- \
  uv run --directory "$PWD" quant-research serve-mcp --store ~/qr-demo/kb.db
```

## 5. Or connect Claude Desktop

Add this to `claude_desktop_config.json` and restart Claude Desktop. The file is in
`~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on Windows. Replace
the paths with your own.

```json
{
  "mcpServers": {
    "quant-research": {
      "command": "/Users/you/.local/bin/uv",
      "args": [
        "run", "--directory", "/Users/you/quant-agent-os",
        "quant-research", "serve-mcp", "--store", "/Users/you/qr-demo/kb.db"
      ]
    }
  }
}
```

Claude Desktop does not read your shell's `PATH`, so give the full path to `uv`. To find it,
run `which uv` on macOS or Linux, or `where uv` on Windows.

## 6. Ask Claude

Try:
- "Using quant-research, what is related to short-term reversal, and what supports it?"
- "Which notes discuss momentum crashes? Quote the passages."
- "What does my corpus say about protein folding?"

For the first question, Claude calls `find_related_signals` and receives:
- **related entities**, each with its graph path, for example
  `signal:short-term-reversal <-predicts- signal:order-flow-imbalance`;
- **passages**, each citing its source and location, for example
  `reversal-and-microstructure.md lines 17-19, § Mechanism`;
- the **corpus snapshot id** the answer was read from.

Claude can then call `get_source_passage` for a passage's full text before quoting it.

For the off-topic question, the server returns no passages and a warning that nothing in the
corpus matched. That is the correct answer, and Claude has nothing to cite.

The server is read-only. It never writes to the knowledge store or calls a network service
unless you choose a hosted `--embedder`. Its tools are marked read-only to the client.

## 7. See what was recorded

Every MCP call is a run in `runs.db` beside the store:

```bash
uv run quant-research runs --store ~/qr-demo/kb.db
uv run quant-research runs <RUN_ID> --store ~/qr-demo/kb.db
```

```text
d49c757b74134b449981363b035d27cd  2026-10-31 00:48:37  mcp   completed  snap:8cddf9d734e07c1c  find_related_signals

Run e48576f0be1849da9fb4677d5069f011 (call, completed) by mcp: find_related_signals
Platform 0.1.0, registry bfa4099bcfd27cfd, data {'corpus_snapshot': 'snap:8cddf9d734e07c1c'}.
  call 4606cc99cee54865bc59b0ffc55b4b76  find_related_signals  ok  input d80b48b797936de3  3.0 ms
  00:48:37  run.started  find_related_signals  {'kind': 'call'}
  00:48:37  tool.called  find_related_signals  {'call_id': '4606…', 'duration_ms': 2.975, 'input_sha256': 'd80b…', 'status': 'ok'}
  00:48:37  run.finished  find_related_signals  {'status': 'completed'}
```

Each run records its run id, call ids, input hashes, the tool registry digest, and the corpus
snapshot, so you can tell exactly which data and tool versions produced an answer. The audit
trail cannot be edited or deleted. Calls include retrieved passages, so treat `runs.db` like
the corpus itself ([security.md](security.md#run-records)). Pass `--no-record` to keep no runs.

## 8. Use your own documents

```bash
uv run quant-research ingest ~/research --store ~/research/.kb.db
```

Point the Claude configuration at the new store. Re-run `ingest` whenever you add or change
files.

To see how the pipeline does on real papers before using your own, the opt-in evaluation
downloads 21 pinned arXiv papers once and scores retrieval and extraction offline
([plans/real-corpus-eval.md](plans/real-corpus-eval.md)):

```bash
uv run python packages/quant-research/tests/real_corpus.py fetch
uv run python packages/quant-research/tests/real_corpus.py eval
```

## 9. From Python

The SDK calls the same tools through the same executor and recorder as MCP:

```bash
uv run python examples/quant-research/sdk_demo.py ~/qr-demo/kb.db
```

```python
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import Platform
from quant_research import open_research, research_tools

with (
    open_research("kb.db", read_only=True) as research,
    Platform(research_tools(research.research), run_store=SqliteRunStore("runs.db")) as platform,
):
    record = platform.call("find_related_signals", query="short-term reversal", k=3)
    run = platform.get_run(record.run_id)
```

## Known limitations

From the real-paper evaluation (21 arXiv papers, 28 gold questions):

| Result | Value |
| --- | --- |
| Topic questions with the right paper in the top 5 | 19 of 19 |
| Paraphrased questions (no shared vocabulary) | 5 of 6 |
| Off-topic questions correctly answered with nothing | 3 of 3 |
| Extracted typed relations that are correct | 0.75 (12 of 16), a small sample |

- **Vocabulary.** Entity and graph retrieval know the 36 entities in the bundled vocabulary:
  factors, signals, strategies, methods, metrics, asset classes, datasets, and concepts.
  Anything else is found only by keyword search.
- **Generic words.** Words that are also everyday English ("profitability", "momentum") can be
  read as the factor.
- **Few typed relations.** Most graph edges in real papers are co-mentions (`related_to`). Typed
  relations need explicit phrasing such as "predicts", "explained by", or "tested on".
- **Paraphrases.** Without a semantic embedder, a question that shares no words with the
  answer can miss. The optional hosted embedders (`--embedder voyage|openai`) need an API key
  and the `network` permission, and have not yet been evaluated against the baseline.
- **PDFs.** Text-layer PDFs only (no OCR). Some kerning artefacts ("ot her") remain, and tables
  are indexed for search but never read as relations.
- **No answers written by the server.** The server returns evidence, and the client (Claude)
  writes the answer. Check its citations against the passages it quotes.
- **Not published.** Packages are not on PyPI, and names are provisional. Run from a checkout
  as shown here.
- **Not investment advice.** This is pre-alpha research software.
