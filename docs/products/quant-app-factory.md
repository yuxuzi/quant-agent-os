# Quant App Factory

**Quant App Factory** is the application-engineering product of Quant-Agent-OS. It turns a
typed application spec into a scaffolded, checked, packaged Python project. It uses
[`pytemplate-uv`](https://pypi.org/project/pytemplate-uv/) 3 as a library (`pytemplate.api`),
behind the replaceable `ScaffoldProvider` port. Only one adapter module imports it.

```text
AppSpec (TOML) ─▶ ArchitecturePlanner ─▶ ScaffoldProvider.plan ─▶ operator approval
               ─▶ ScaffoldProvider.create ─▶ checks (uv sync, ruff, mypy, pytest) ─▶ FactoryReport
```

| `template` and `profile` | Built from | Depends on Quant-Agent-OS |
| --- | --- | --- |
| `agent-tool`, `agent-tool` | this package's template pack (`quant_app_factory/packs/agent-tool`): one typed tool, a registry, and `check_tool` contract tests | Yes, on purpose |
| `bundled`, `python` | pytemplate-uv's bundled `python` profile | No |
| `bundled`, `fastapi` | pytemplate-uv's bundled `fastapi` profile (`database`, `worker`, `auth`, ...) | No |
| `quant-apps`, `alpha-research` | the `quant-apps` pack: signal evidence around the firm's signal, and a signal file | No |
| `quant-apps`, `strategy-backtest` | the `quant-apps` pack: signals or target weights to net returns, with walk-forward | No |
| `quant-apps`, `risk-monitor` | the `quant-apps` pack: positions to exposures, value at risk, stress, and limit checks | No |
| `quant-apps`, `performance-report` | the `quant-apps` pack: returns to horizon tables, risk basics, tear sheets, Excel, and reconciliation | No |
| `quant-baseline`, `quant-lab` | the `quant-baseline` pack: one project with `study` (IC, rank IC, spreads, decay), `backtest` (lagged trades, drift, rebalancing, costs, delistings), and `report` commands, sharing one `config.toml` | No (optional `agent-tools`) |
| `path/to/pack`, your profile | your own pack | Up to the pack |

On the command line, `qaos app new` also takes the shortcuts `alpha-research`, `strategy-backtest`,
`risk-monitor`, `performance-report`, `agent-tool`, `quant-lab`, `python`, and `fastapi`.

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
| 3 | Add your own signals, rules, sections, checks, and file formats | the project's `firm/` folder, which template updates skip |
| 4 | Change anything else | Any file: you own the code, and its known-answer tests catch mistakes |

Each project's `AGENTS.md` gives a coding assistant the same rules. The `agent-tools` component serves an application's
calculations to AI clients through a firm's own server. The templates supply no alpha: momentum and reversal are textbook
examples, and a firm's named signal is tested for look-ahead before each run.

A quant-lab project has one `metrics` module, with documented conventions and
known-answer tests. Each also has bias tests (no look-ahead, a real trade lag, costs that only
cost) and a `LIMITATIONS.md`. Optional components are `data-parquet`, `data-database`
(PostgreSQL, SQLite, or DuckDB, read-only, with a `check-connection` command), and `agent-tools`. A
spec can name a connection saved in `factory.toml` (`connection = "research-pg"`).

## Generated metrics and the platform's

A quant-lab project owns a numpy copy of its metrics,
so it stays portable ([decision 0009](../decisions/0009-quant-baseline-templates.md)).
`tests/factory/test_metric_parity.py` holds that copy to `quant_analytics.metrics` on annualised
return, volatility, Sharpe, maximum drawdown, hit rate, beta, active return, tracking error, and
information ratio. They differ in three ways:
- An undefined metric is NaN in a generated project and None on the platform.
- A generated metric drops missing values itself, one series at a time, except `active_metrics` and
  `beta`, which use bars where both are present. The platform's tools leave out and count missing
  rows first, so its metric functions never see them.
- Each has metrics the other lacks: Newey-West t-statistics, information coefficients, and turnover
  only in generated projects; Sortino, Calmar, omega, tail ratio, stability, value at risk, and alpha
  only on the platform.

A generated project does not receive later fixes by itself, and its `LIMITATIONS.md` says so.
`pytemplate update` cannot reach one built from a built-in pack, because the project records a local
path with no tags: apply a fix by hand, using [CHANGELOG.md](../../CHANGELOG.md) to see what changed.

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
uv run qaos app template add gh:org/packs --name firm --ref v1.0.0 # pinned git pack
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
from git, pinned to a tag and cached locally, so plans stay offline.

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
