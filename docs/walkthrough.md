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

## 10. Generate a tool package

Quant App Factory creates a new, tested project from a small spec. Let it ask the questions
(`uv run quant-factory new`), or write the spec yourself:

```bash
cat > ~/qr-demo/signal-tools.toml <<'TOML'
name = "signal-tools"
kind = "agent-tool"
description = "Signal tools for Quant-Agent-OS"
output_dir = "."
tool_name = "count_signals"
quant_agent_os_source = "/path/to/quant-agent-os"   # this checkout; omit to use the git repository
TOML
uv run quant-factory plan ~/qr-demo/signal-tools.toml
uv run quant-factory create ~/qr-demo/signal-tools.toml
```

What the two commands do:
- **`plan`** lists the profile, the reasons, the dependencies, and every file it would write.
- **`create`** asks first:
  - your yes is recorded as a single-use approval for that exact spec;
  - it then runs `uv sync`, ruff, mypy, and pytest in the new project;
  - its tests include `check_tool`, the platform's contract checks.

`kind = "quant-research"` builds a portable quant project from the `quant-baseline` pack: a
signal study, a backtest, and a report in one project, with known-answer metric tests, bias
tests, and a `LIMITATIONS.md`. `uv run <project> study --demo` runs it on a random walk.
`kind = "python-cli"` or `"fastapi-service"` builds a portable app from pytemplate-uv's own
profiles. Creating needs network access for pytemplate-uv and the project's dependencies.

## 11. A quant project from your own data, with guidance

This section goes from saved settings to a study, a backtest, and a report on prices in a
database, all in one project, then makes the template your own. It was run end to end on
2026-11-01; the outputs shown are from that run. The prices were 500 weekdays of random walks
for 20 assets in a SQLite file (`date` as ISO text, then `asset` and `price`), so finding no
edge is the expected result.

**1. Save who you are and where your prices live** (in `~/.quant-agent-os/factory.toml`,
never secrets):

```bash
uv run quant-factory settings set identity.author "Ada Lovelace"
uv run quant-factory settings set connections.local-prices.kind sqlite
uv run quant-factory settings set connections.local-prices.database "$PWD/prices.db"
```

For PostgreSQL, also set `host`, `port`, `user`, and `schema`. Set `password_env` to the
*name* of the environment variable that holds the password, never the password itself.

**2. Answer the template's questions.** `new` offers saved connections and skips questions
that do not apply:

```bash
uv run quant-factory new --template quant-baseline -o lab.toml
```

```text
Profile: quant-research (One project for signal research: ...)
Ready: 34 files.
```

```toml
name = "rev-lab"
template = "quant-baseline"
profile = "quant-research"
connection = "local-prices"
description = "Reversal research on local prices"
output_dir = "/Users/you/work"
components = ["data-database"]
```

**3. Create it, and check the connection.** Your yes is the recorded approval:

```bash
uv run quant-factory create lab.toml --check-connection
```

```text
  ok  uv sync
  ok  uv run ruff check .
  ok  uv run ruff format --check .
  ok  uv run mypy rev_lab tests
  ok  uv run pytest
  ok  uv run rev-lab check-connection
```

**4. Set the research parameters** in the project's `config.toml`. Every run reads it, and
flags such as `--lag 0` override it for one run:

```toml
signal = "reversal"
lookback = 5
lag = 1          # bars from the signal's close to the trade, shared by study and backtest
cost_bps = 2
rebalance_every = 21
```

**5. Study, backtest, and report** the database prices:

```bash
cd rev-lab
uv run rev-lab study --database      # writes results/study.json
uv run rev-lab backtest --database   # writes results/backtest.json
uv run rev-lab report results/backtest.json --out report.md
```

The study finds a mean rank IC of −0.016, with a Newey-West t-statistic of −1.58: no signal,
as expected. It scores the return from the trade bar, one bar after the signal, which is what
the backtest can earn. The report:

```text
# reversal (lookback 5) long-short backtest
- Period: 2023-01-02 to 2024-11-29 (500 bars, 20 assets)
- Parameters: cost_bps = 2, delisting_return = -0.3, lag = 1, lookback = 5, rebalance_every = 21, signal = reversal
| average turnover | 3.14% |
| forced exits | 0 |
| max drawdown | -2.79% |
| sharpe ratio | 0.8973 |
## Interpretation
_Not written yet. Nothing above this section is interpretation._
```

A Sharpe ratio of 0.9 over two years is within noise: its standard error is about 0.7. The
book rebalances monthly, so turnover is about a tenth of a daily book's.

**6. Make the template yours.** Fork it, edit it (by hand or with an AI assistant), and use
it by name:

```bash
uv run quant-factory template fork quant-baseline/quant-research packs/desk-research --profile-id desk
#   ok  desk (31 files)
#   Registered template 'desk-research': use template = "desk-research" in a spec.
uv run quant-factory template check desk-research   # after each edit
```

Each part of the pack has one job:
- **`questions.yml`:** add a question there and list it in `desk/profile.toml`.
- **`template/`:** add or edit files there, and use answers as `{{ name }}`.
- **`components/`:** add a component there.

`quant-factory template add gh:your-org/quant-packs --name firm --ref v1.0.0` registers a pack
from git, pinned to a tag and cached locally, so plans stay offline.

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
- **App Factory scope.** The factory scaffolds and checks, but it does not write your code yet.
  Specs are written by hand, because model drafting (B-16) and code generation (B-20) are
  planned. Approvals come from the SDK or the factory CLI, not from an MCP client.
- **Not investment advice.** This is pre-alpha research software.
