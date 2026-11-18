# Quant App Factory

**Quant App Factory** is the optional application-engineering product of Quant-Agent-OS, off by
default on the server. It turns a typed application spec into a scaffolded, checked, packaged Python project. It uses
[`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) 3.1 or later as a library (`pytemplate.api`),
behind the replaceable `ScaffoldProvider` port. Only one adapter module imports it.

```text
AppSpec (TOML) ─▶ ArchitecturePlanner ─▶ ScaffoldProvider.plan ─▶ operator approval
               ─▶ ScaffoldProvider.create ─▶ checks (uv sync, ruff, mypy, pytest) ─▶ FactoryReport
```

| `template` and `profile` | Built from | Depends on Quant-Agent-OS |
| --- | --- | --- |
| `agent-tool`, `agent-tool` | this package's template pack (`quant_app_factory/packs/agent-tool`): one typed tool, a registry, and `check_tool` contract tests | Yes, on purpose |
| `bundled`, `python` | pytemplate-uv's bundled `python` profile (`container`, `devcontainer`, `mcp`, `release`) | No |
| `bundled`, `library` | pytemplate-uv's bundled `library` profile: a typed package without a command line (`devcontainer`, `mcp`, `release`) | No |
| `bundled`, `fastapi` | pytemplate-uv's bundled `fastapi` profile (`database`, `worker`, `auth`, `devcontainer`, ...) | No |
| `quant-apps`, `alpha-research` | the `quant-apps` pack: signal evidence around the firm's signal (IC, quantile returns, optional winsorizing and z-scores), and a signal file | No |
| `quant-apps`, `strategy-backtest` | the `quant-apps` pack: signals or target weights to net returns, with purged walk-forward and the cost drag and break-even cost | No |
| `quant-apps`, `risk-monitor` | the `quant-apps` pack: positions to exposures, value at risk, stress (including twelve historical windows), and limit checks | No |
| `quant-apps`, `performance-report` | the `quant-apps` pack: returns to horizon tables, risk basics, tear sheets, Excel, and reconciliation | No |
| `quant-baseline`, `quant-lab` | the `quant-baseline` pack: one project with `study` (IC, rank IC, spreads, decay), `backtest` (lagged trades, drift, rebalancing, costs, delistings), and `report` commands, sharing one `config.toml` | No (optional `agent-tools`) |
| `path/to/pack`, your profile | your own pack | Up to the pack |

On the command line, every template is `pack/profile`, such as `qaos app new quant-apps/risk-monitor my-risk`.
`qaos app template list` prints those names. A built-in profile's own name also works: `alpha-research`,
`strategy-backtest`, `risk-monitor`, `performance-report`, `agent-tool`, `quant-lab`, `python`, `library`, and `fastapi`.

The `release` component adds a PyPI trusted-publishing workflow for `v*` tags. To see which files of a
generated project changed since it was created, run `uvx pytemplate-uv status` in it; it never writes.

For tools an AI client calls, choose by where they run. `agent-tool` builds tools for a Quant-Agent-OS server,
behind its permissions, approvals, and audit. `python` with the `mcp` component builds a standalone MCP server
that does not depend on the platform.

## Where templates come from

| Source | Named | It changes when |
| --- | --- | --- |
| Built in: `quant-apps`, `quant-baseline`, `agent-tool`, and pytemplate-uv's `bundled` profiles | `quant-apps/risk-monitor` | You upgrade `quant-agent-os` |
| Your own pack: a fork of a built-in profile, or any pack directory | The name you registered | You edit it |
| A git pack, yours or someone else's | The name you registered | Never. It is fetched once at a tag and held to that commit and a digest of its files |

A generated project is a starting point, and it is yours from the first commit
([decision 0025](../decisions/0025-templates-are-starting-points.md)). No command upgrades it from a newer
template. Its `.pytemplate.toml` records the pack, the pack's version, and your answers. To take a later fix,
generate a fresh project from the newer template with those answers, and compare the two.

Read a pack you did not write before you use it. Rendering runs no pack code, but the generated project's
dependencies and tests run when you check it.

## Applications for each stage of a fund's work

The `quant-apps` pack has one application for each stage ([decision 0022](../decisions/0022-one-application-per-stage.md)).
Each works as generated and is meant to be changed:

```text
alpha-research ──▶ strategy-backtest ──▶ performance-report
 (scores.csv)       (returns.csv)         (tables, tear sheets, reconciliation)
                    (weights.csv) ──────▶ risk-monitor (exposures, stress, limits)
```

The stages exchange ordinary CSV files with a date column, so you can use one alone or replace any of them.

| Level | What you do | Where |
| --- | --- | --- |
| 1 | Set conventions and data | `config.toml` |
| 2 | Choose what appears, such as each audience's tear sheet | `config.toml` |
| 3 | Add your own signals, rules, sections, checks, and file formats | the project's `firm/` folder |
| 4 | Change anything else | Any file: you own the code, and its known-answer tests catch mistakes |

Each project's `AGENTS.md` gives a coding assistant the same rules. The `agent-tools` component serves an application's
calculations to AI clients through a firm's own server. The templates supply no alpha: momentum and reversal are textbook
examples, and a firm's named signal is tested for look-ahead before each run.

A quant-lab project has bias tests (no look-ahead, a real trade lag, costs that only cost) and a
`LIMITATIONS.md`. Optional components are `data-parquet`, `data-database` (PostgreSQL, SQLite, or DuckDB,
read-only, with a `check-connection` command), and `agent-tools`. A spec can name a connection saved in
`factory.toml` (`connection = "research-pg"`).

## Formulas come from Quant Toolbox

A generated project holds its business logic: its settings, its `firm/` code, its commands, the order in
which it runs things, and its report sections. Its formulas, tables, reports, charts, and run records come
from [Quant Toolbox](quant-toolbox.md), the library Quant Analytics uses too
([decision 0026](../decisions/0026-quant-toolbox.md)). Its `pyproject.toml` depends on
`quant-toolbox[charts]` (performance-report adds `excel`) from the release that generated it
([decision 0027](../decisions/0027-quant-toolbox-package.md)):

```toml
[tool.uv.sources]
quant-toolbox = { git = "https://github.com/yuxuzi/quant-agent-os", rev = "v0.6.0", subdirectory = "packages/quant-toolbox" }
```

So its numbers change only when you move that tag. To take a later fix to a formula or a report, edit `rev`,
then run `uv sync` and the project's tests. A fix to the application's own code still arrives the way
[above](#where-templates-come-from) says: compare with a fresh project. The project installs only the MIT
toolbox, and `quant-agent-os` only with `agent-tools`. Charts are static SVG; set
`charts = "interactive"` in `config.toml` and run `uv add "quant-toolbox[charts,interactive]"` for plotly charts.

To use another definition of a figure, add a separately named one in the project, so that both keep theirs.

What moved to the toolbox, measured in lines of each template's package and tests (Python and Jinja files,
0.4.0 against 0.5.0):

| Application | Package | Tests |
| --- | --- | --- |
| alpha-research | 1,609 to 643 | 551 to 372 |
| strategy-backtest | 2,067 to 969 | 958 to 509 |
| risk-monitor | 1,876 to 1,039 | 447 to 275 |
| performance-report | 2,075 to 954 | 785 to 343 |
| quant-lab | 2,092 to 1,154 | 1,334 to 710 |

The tests that left check the toolbox, in `packages/quant-toolbox/tests`; a project keeps the tests of its own code.

## Use

```bash
uv run qaos app new performance-report my-report --defaults   # one command: spec, project, checks
uv run qaos app new quant-lab my-lab --defaults   # or the all-in-one research sample
uv run qaos app new                # answer the template's questions; writes a ready spec
uv run qaos app plan app.toml      # writes nothing; --verbose lists every file
uv run qaos app create app.toml    # asks; your yes is the recorded, single-use approval
uv run qaos app check app.toml     # re-run an existing project's checks
uv run qaos app settings set identity.author "Ada Lovelace"   # ~/.quant-agent-os/factory.toml
uv run qaos serve --toolsets factory          # describe_template, check_spec for Claude
uv run qaos app template fork quant-baseline/quant-lab packs/mine   # make it yours
uv run qaos app template list                     # every template, as pack/profile
uv run qaos app template add gh:org/packs --name firm --ref v1.0.0 --commit SHA --digest HASH
```

A small spec, which `app plan` previews and `app create` builds:

```bash
cat > signal-tools.toml <<'TOML'
name = "signal-tools"
template = "agent-tool"      # or quant-baseline, bundled (python, fastapi), or a pack path
profile = "agent-tool"
description = "Signal tools for Quant-Agent-OS"
output_dir = "."
tool_name = "count_signals"
TOML
uv run qaos app plan signal-tools.toml
uv run qaos app create signal-tools.toml
```

Secrets never go in a spec or `factory.toml`. Use `env:NAME` in a spec's `[settings]`, and
`password_env` or `url_env` for a connection.

## A quant project from your own data

This section goes from saved settings to a study, a backtest, and a report on prices in a
database, all in one project, then makes the template your own. It was run end to end on
2026-11-01; the outputs shown are from that run. The prices were 500 weekdays of random walks
for 20 assets in a SQLite file (`date` as ISO text, then `asset` and `price`), so finding no
edge is the expected result.

**1. Save who you are and where your prices live** (in `~/.quant-agent-os/factory.toml`,
never secrets):

```bash
uv run qaos app settings set identity.author "Ada Lovelace"
uv run qaos app settings set connections.local-prices.kind sqlite
uv run qaos app settings set connections.local-prices.database "$PWD/prices.db"
```

For PostgreSQL, also set `host`, `port`, `user`, and `schema`. Set `password_env` to the
*name* of the environment variable that holds the password, never the password itself.

For a first look, `uv run qaos app new quant-lab my-lab --defaults` takes every
default and creates the project after one confirmation (`--yes` skips it in scripts). Steps 2
and 3 do the same with your own answers.

**2. Answer the template's questions.** `new` offers saved connections and skips questions
that do not apply:

```bash
uv run qaos app new --template quant-baseline -o lab.toml
```

```text
Profile: quant-lab (One project for signal research: ...)
Ready: 34 files.
```

```toml
name = "rev-lab"
template = "quant-baseline"
profile = "quant-lab"
connection = "local-prices"
description = "Reversal research on local prices"
output_dir = "/Users/you/work"
components = ["data-database"]
```

**3. Create it, and check the connection.** Your yes is the recorded approval:

```bash
uv run qaos app create lab.toml --check-connection
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
flags such as `--lag 0` override it for one run. With the database component, `data` already
names the database:

```toml
data = "database"
signal = "reversal"
lookback = 5
lag = 1          # bars from the signal's close to the trade, shared by study and backtest
cost_bps = 2
rebalance_every = 21
```

**5. Study, backtest, and report** the database prices:

```bash
cd rev-lab
uv run rev-lab study          # Mean rank IC -0.0161 (Newey-West t -1.58) over 500 bars.
uv run rev-lab backtest       # Net Sharpe 0.90, ...
uv run rev-lab walk-forward   # out-of-sample only
uv run rev-lab report         # renders the latest run
```

Each run writes its own folder, such as `results/20261101T134801Z-backtest-reversal/`. It
holds `results.json`, with the metrics, parameters, a SHA-256 fingerprint of the prices, and
the code version, and `config.toml`, the exact parameters. `--config` pointing at that file
repeats the run, and no run overwrites another.

The study finds no signal, as expected. It scores the return from the trade bar, one bar after
the signal, which is what the backtest can earn. The report:

```text
# reversal (lookback 5) long-short backtest
- Period: 2023-01-02 to 2024-11-29 (500 bars, 20 assets)
- Data fingerprint: sha256:...
- Code version: 0.1.0
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
uv run qaos app template fork quant-baseline/quant-lab packs/desk-research --profile-id desk
#   ok  desk (31 files)
#   Registered template 'desk-research': use template = "desk-research" in a spec.
uv run qaos app template check desk-research   # after each edit
```

Each part of the pack has one job:
- **`questions.yml`:** add a question there and list it in `desk/profile.toml`.
- **`template/`:** add or edit files there, and use answers as `{{ name }}`.
- **`components/`:** add a component there.

`qaos app template add gh:your-org/quant-packs --name firm --ref v1.0.0` registers a pack
from git, fetched once and cached locally, so plans stay offline. It prints and records the commit and a digest
of the files. Give those to colleagues: with `--commit` and `--digest`, their `template add` refuses anything
else, even if the tag is moved.

## Legacy code as tools

`qaos legacy` turns a fund's existing Python into a draft of tools, on the developer's machine
([decision 0029](../decisions/0029-legacy-conversion-is-a-local-draft.md)). The draft is a first version for a
developer to run, debug, and finish, most likely with a coding agent and the `quant-legacy` skill. It will not
work first time on real code. It says what it could not decide, and its own test says when it works.

```bash
uv run qaos legacy scan src/firm_quant                        # a table: kind and reasons per callable
uv run qaos legacy scan src/firm_quant --json                 # the same, for machines
uv run qaos legacy scan src/firm_quant --write legacy.toml    # an inventory to edit
uv run qaos legacy wrap legacy.toml --out legacy_tools        # the draft; never overwrites without --force
uv run pytest legacy_tools/test_tools.py                      # fix, rerun, until both tests pass
```

**Scan** reads the code with `ast` and never imports it, so code that needs the fund's database still scans.
For each public function and method it records the signature, which parameters and returns are typed, the
docstring, and what the body does: pandas or NumPy data, module settings, environment variables, a database,
files read or written, printing, network calls, programs run, state kept between calls, and a class's state,
slow set-up, and caches. A private helper's behaviour counts for its callers. Each callable gets a kind:

| Kind | When | The draft |
| --- | --- | --- |
| `expose` | Typed, documented, pure, with types `FirmApp.expose` accepts | `app.expose(function, ...)` in `server.py` |
| `facade` | Untyped or undocumented, reads settings, prints, touches files or a database, or is a method of a class without costly state | A thin typed function in `facades.py` that calls the legacy code unchanged |
| `service` | Keeps state between calls, starts slowly, or caches | `service/app.py`, a FastAPI app that builds the object once and answers read-only POST requests, and a typed stub per endpoint in `stubs.py`, registered with `FirmApp.call_service` |

**The inventory** (`legacy.toml`) has one `[[entry]]` per callable, with the scan's reasons as comments above
it. The developer sets `kind`, `include`, `name`, `data` (parameters that take a dataset reference, `"series"`
or `"frame"`), `permissions`, and `convention`. `wrap` scans the code again, so the draft follows the code as
it is now.

**Wrap** writes `facades.py`, `server.py` (a `build_app` that names every tool), `service/app.py` and `stubs.py`
when there are services, `conftest.py` (puts the legacy package on the path until it is installed), and
`test_tools.py`. The test runs `check_app` on every tool with an example call over made-up sample data, and
fails while any `TODO(...)` is left in the draft. The generated code passes `ruff check` as written.
- **TODOs say what to decide**: `TODO(type)` for a guessed hint, `TODO(data)` for a guessed series or frame,
  `TODO(unit)` and `TODO(convention)` for what a number means, `TODO(config)` for module settings and
  constructor arguments, `TODO(doc)` for a missing description, and `TODO(review)` for permissions, files,
  databases, and thread safety.
- **Permissions follow the code.** A function that writes files is drafted with `write_local_data`, one that
  calls out with `network`, one that runs programs with `execute_code`, and a service that changes anything
  with `external_action`. Declared permissions are checked, not sandboxed: review them, and add any the scan
  missed.
- **Facades stay thin.** They add hints, a docstring, data by reference, prints sent to stderr (stdout carries
  the MCP protocol over stdio), and JSON-ready results, with missing values as null. Business logic stays in
  the legacy code.

**What to expect.** On the converter's test fixture, a typed function, an untyped pandas signal that reads a
module `CONFIG` and prints, a CSV writer, and a risk model with a slow start and a cache, `wrap` drafts five
tools and the generated `check_app` test passes as written, the service called over loopback; 27 TODOs are
left to decide. Real code will need more: the scan misses what only running would show, such as a method called
on an object passed in, or a library it does not know. A rescan rewrites the inventory, so write it to another
file to keep your edits.

## From Python

From Python, `plan_app` is read-only: pytemplate-uv renders its file preview into a
temporary directory. `create_app` declares `write_local_data`,
`execute_code`, and `network`, so each call waits for an operator approval:

```python
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory import AppFactory, PytemplateUvScaffolder, factory_tools
from quant_app_factory.tools import CREATE_PERMISSIONS

policy = PermissionPolicy(granted=READ_ONLY | CREATE_PERMISSIONS)
with Platform(factory_tools(AppFactory(PytemplateUvScaffolder())), policy=policy) as platform:
    waiting = platform.call("create_app", spec={...})  # status "approval_required"
    platform.approve(waiting.run_id)  # the operator says yes
    (created,) = platform.resume(waiting.run_id)  # runs once, in the same run
```

The design is in [architecture.md](../architecture.md#quant-app-factory).
