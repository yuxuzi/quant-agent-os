# Quant Toolbox

**Quant Toolbox** (`quant_toolbox`) is the library under Quant Analytics and every application the App
Factory generates: tables and price panels, performance and risk metrics, signal research and backtests,
reports and charts, and run records. A formula, a table reader, or a report renderer exists once, here
([decision 0026](../decisions/0026-quant-toolbox.md)). It is MIT licensed, imports nothing of the platform,
and needs only numpy.

```bash
uv add "quant-agent-os[toolbox] @ git+https://github.com/yuxuzi/quant-agent-os@v0.5.0"
```

## What it holds

Seven subpackages in layers; each imports only the ones above it.

| Layer | Subpackage | Holds |
| --- | --- | --- |
| 1 | `quant_toolbox.data` | `Table` and `Panel`; reading and saving tables with a `.meta.json` record of where they came from; configuration files read into a dataclass, with every mistake named; checks on returns (short samples, percentages, gaps, outliers); synthetic prices, returns, and books |
| 2 | `quant_toolbox.performance` | returns from prices; annualised return and volatility, Sharpe, Sortino, Calmar, omega, maximum drawdown, tail ratio, stability, value at risk, expected shortfall, hit rate, turnover; alpha, beta, and active metrics against a benchmark; horizons (MTD to ITD), monthly and calendar-year returns, drawdown episodes |
| 3 | `quant_toolbox.risk` | a book's exposures and concentration, group exposures, simulated history, Euler volatility contributions and risk shares, factor exposures and variance shares, linear stress |
| 3 | `quant_toolbox.research` | ranks, information coefficients, Newey-West t-statistics, forward returns with a trade lag, quantile spreads, IC decay; 12-1 momentum and reversal; dollar-neutral, beta-neutral, and long-only weights; the backtest engine and walk-forward splits |
| 4 | `quant_toolbox.report` | a report as data (tables, charts, text) rendered to Markdown, HTML, or JSON; chart builders; run reports and comparisons; metric formatting |
| 4 | `quant_toolbox.audit` | provenance, code versions, run folders that are never reused, JSON-safe results |
| 5 | `quant_toolbox.testing` | bias checks for a project's own signals and strategies: no look-ahead, the future never changes the past, costs only cost, no edge on random walks |

Import from a subpackage, never from a file whose name starts with an underscore:

```python
from pathlib import Path

from quant_toolbox.data import load_table
from quant_toolbox.performance import max_drawdown, sharpe_ratio
from quant_toolbox.report import Document, equity_curve, render_html

returns = load_table(Path("returns.csv"))
fund = returns.column("fund")
print(sharpe_ratio(fund, 252), max_drawdown(fund))
chart = equity_curve(returns.dates, {"fund": fund})
Path("report.html").write_text(render_html(Document("Fund", blocks=[chart])))
```

Every convention is stated in the module that computes it, and `performance.conventions()` returns them as
text for a report or a tool's output. An undefined metric is NaN; Quant Analytics shows it as `null`.

## empyrical first, numpy otherwise

The metrics empyrical-reloaded has are computed by it when it is installed, and by the toolbox's numpy
formulas when it is not. Before calling empyrical the toolbox drops missing values, answers NaN for a series
that does not vary, and turns infinities into NaN, so the two give the same number:
`tests/toolbox/test_backends.py` holds them to 1e-12 on clean data and on these cases, measured on
2026-11-17, where empyrical called directly would differ:

| Input | Metric | numpy | empyrical, called directly |
| --- | --- | --- | --- |
| a constant 1% return | Sharpe | NaN | 4.5e16 |
| no negative returns, or two points | Sortino | NaN | inf |
| 504 returns with 11 missing | annualised return | -21.15% | -20.74% |
| 504 returns with 11 missing | value at risk | -1.64% | NaN |

`performance.backend()` says which runs. Set `QUANT_TOOLBOX_BACKEND=numpy` (or pass `backend="numpy"`) to use
one implementation on every machine. Every toolbox test runs under both.

## Optional parts

Only numpy is needed to import any module. Everything else loads in the function that uses it; a missing
library raises `MissingExtraError` with the command that installs it, and `quant_toolbox.features()` says
what is installed.

| Extra | Brings | For |
| --- | --- | --- |
| `toolbox` | numpy, pygal (532 KB, pure Python) | everything, with static SVG charts |
| `interactive` | plotly (38 MB) | `render_html(..., charts="interactive")`: plotly.js inline once, so the page works offline; `"interactive-cdn"` links it instead |
| `excel` | openpyxl | `data.excel.read_workbook`, `report.excel.write_workbook` |
| `empyrical` | empyrical-reloaded, with pandas and scipy (138 MB) | the empyrical backend |

Static charts carry no script and no link: a test checks every SVG. pandas is never required;
`data.frames.to_pandas` and `from_pandas` import it for notebooks.

## Stability

A generated project pins the release that generated it, so its numbers change only when its owner moves the
tag. `tests/toolbox/api.lock.json` records every public name and signature, and a change fails the test
until the lock is rewritten and [CHANGELOG.md](../../CHANGELOG.md) says what changed. While the toolbox is
0.x, a removed or changed name is a minor version bump; after 1.0, a major one.

To take a newer toolbox in a generated project, edit the `rev` under `[tool.uv.sources]` in its
`pyproject.toml`, then run `uv sync` and its tests.
