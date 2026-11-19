# Quant Toolbox

**Quant Toolbox** (`quant_toolbox`) is the library under Quant Analytics and every application the App
Factory generates: tables and price panels, performance and risk metrics, signal research and backtests,
reports and charts, and run records. A formula, a table reader, or a report renderer exists once, here
([decision 0026](../decisions/0026-quant-toolbox.md)). It is MIT licensed, imports nothing of the platform,
and needs only numpy. It is its own package, `quant-toolbox`, in `packages/quant-toolbox`, with its own
version and [changelog](../../packages/quant-toolbox/CHANGELOG.md)
([decision 0027](../decisions/0027-quant-toolbox-package.md)):

```bash
uv add "quant-toolbox[charts] @ git+https://github.com/yuxuzi/quant-agent-os@v0.7.0#subdirectory=packages/quant-toolbox"
```

The platform's `toolbox` extra installs the same package, so `quant-agent-os[toolbox]` still works.

## What it holds

Seven subpackages in layers; each imports only the ones above it.

| Layer | Subpackage | Holds |
| --- | --- | --- |
| 1 | `quant_toolbox.data` | `Table` and `Panel`; reading and saving tables with a `.meta.json` record of where they came from; configuration files read into a dataclass, with every mistake named; checks on returns (short samples, percentages, gaps, outliers); synthetic prices, returns, and books |
| 2 | `quant_toolbox.performance` | `analyze`, every measure of a series in one typed, JSON-safe result; returns from prices; annualised return and volatility, Sharpe, Sortino, Calmar, omega, maximum drawdown, tail ratio, stability, historical and parametric (Gaussian, Cornish-Fisher) value at risk and expected shortfall, hit rate, turnover; alpha, beta, active metrics, and up and down capture against a benchmark; rolling volatility, Sharpe, beta, and correlation; skewness, kurtosis, and the distribution of returns; the Sharpe ratio's standard error, probabilistic and deflated Sharpe, and minimum track record; single-period Brinson attribution; horizons (MTD to ITD), monthly and calendar-year returns, drawdown episodes with their lengths |
| 3 | `quant_toolbox.risk` | a book's exposures and concentration (Herfindahl), group exposures, simulated history; liquidity (days to liquidate at a share of ADV, Form PF-style buckets); Euler volatility contributions from a sample, EWMA, or vendor covariance, and expected-shortfall contributions; factor exposures and variance shares, a vendor factor model's risk, returns-based style regression with Newey-West t-statistics; correlation summaries; linear and historical-scenario stress, with twelve named windows from 2007 to 2024 |
| 3 | `quant_toolbox.research` | ranks, information coefficients, Newey-West t-statistics, forward returns with a trade lag, quantile returns and spreads, IC decay and IC information ratio; z-scores, winsorizing, and group or factor neutralization; 12-1 momentum and reversal; dollar-neutral, beta-neutral, and long-only weights; the backtest engine with per-asset costs, cost drag and break-even cost; walk-forward and purged k-fold splits |
| 4 | `quant_toolbox.report` | a report as data (tables, charts, text) rendered to Markdown, HTML, or JSON; the investor tear sheet and monthly grid; chart builders; run reports and comparisons; metric formatting |
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

For one series, `analyze` gives everything at once, and `tear_sheet` turns it into the investor one-pager:

```python
from quant_toolbox.performance import analyze
from quant_toolbox.report import tear_sheet

result = analyze(returns.dates, fund, 252, benchmark=returns.column("index"))
print(result.metrics["sharpe_ratio"], result.sharpe_inference["probabilistic_sharpe_ratio"])
document = tear_sheet(returns.dates, fund, 252, name="Fund", benchmark=returns.column("index"))
Path("tear-sheet.html").write_text(render_html(document))
```

Its returns table has a row for the fund, the benchmark, and each series in `compare=` (more benchmarks or
peers), then the fund's excess over each, for the last day, MTD, QTD, YTD, 1Y, 3Y, 5Y, and ITD, or the
`periods=` given. `returns_table` is the same section for any report. With the `pdf` extra,
`quant_toolbox.report.pdf.write_pdf(document, Path("tear-sheet.pdf"))` writes it as a PDF; without it, the
call says which extra to install.

`factsheet` builds a fund's monthly factsheet (facts, returns by period and calendar year, risk measures
over 1, 3, and 5 years, rolling volatility, top holdings, and a breakdown by group), and `risk_report` a
book's risk report (exposure, value at risk and expected shortfall, contribution to risk, stress,
liquidity, factor risk, and correlation). Each section is also a function of its own, so a firm composes
its own report as a `Document` of the sections it wants, and `write_report` writes any document by its
file's suffix:

```python
from quant_toolbox.report import factsheet, write_report

document = factsheet(
    returns.dates,
    fund,
    252,
    name="Fund",
    benchmark=returns.column("index"),
    facts={"Currency": "USD"},
    disclaimer="For professional investors only.",
)
write_report(document, "factsheet.pdf")  # or .html, .md, .json, .xlsx
```

## Where it fits

Measured on 2026-11-17 from each project's PyPI metadata, every other open-source performance library requires
pandas or a plotting library at install: empyrical-reloaded (pandas, scipy), quantstats (matplotlib, seaborn,
yfinance), pyfolio-reloaded and alphalens-reloaded (matplotlib, seaborn), ffn (matplotlib, scikit-learn), and
jquantstats (polars, plotly). Quant Toolbox requires numpy alone, states every convention, and returns NaN with
a note rather than a number it cannot defend. Most issues filed against those libraries in 2026 are about
undocumented conventions and silently wrong numbers.

What it leaves to others, on purpose:

- **Portfolio optimisation** (mean-variance, HRP, Black-Litterman): skfolio or Riskfolio-Lib, which need cvxpy.
- **Estimating factor risk models**: a vendor's (Axioma, Barra, Bloomberg) loadings and covariance go into
  `risk.factor_risk` and `risk.euler_contributions`; the toolbox decomposes them and estimates none.
- **Event-driven backtests, order simulation, and live trading**: zipline-reloaded, vectorbt, nautilus_trader.
- **Market data download**: tables are read from the firm's own files.
- **GIPS composites, derivatives pricing, multi-period attribution linking**: firm processes or QuantLib.

## empyrical first, numpy otherwise

The metrics empyrical-reloaded has are computed by it when it is installed, and by the toolbox's numpy
formulas when it is not. Before calling empyrical the toolbox drops missing values, answers NaN for a series
that does not vary, and turns infinities into NaN, so the two give the same number:
`packages/quant-toolbox/tests/test_backends.py` holds them to 1e-12 on clean data and on these cases, measured on
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

Each is an extra of `quant-toolbox`; the platform's extra of the same purpose (`toolbox` for `charts`)
installs it.

| Extra | Brings | For |
| --- | --- | --- |
| `charts` | pygal (532 KB, pure Python) | static SVG charts in reports |
| `interactive` | plotly (38 MB) | `render_html(..., charts="interactive")`: plotly.js inline once, so the page works offline; `"interactive-cdn"` links it instead |
| `excel` | openpyxl | `data.excel.read_workbook`, `report.excel.write_workbook` |
| `pdf` | fpdf2 (pure Python), with Pillow and fontTools (28 MB) | `report.pdf.render_pdf` and `write_pdf`: A4, tables and charts drawn as vectors from the document's data |
| `empyrical` | empyrical-reloaded, with pandas and scipy (138 MB) | the empyrical backend |

Static charts carry no script and no link: a test checks every SVG. pandas is never required;
`data.frames.to_pandas` and `from_pandas` import it for notebooks (the `pandas` extra), and `all` brings
every extra.

## Stability

A generated project pins the release that generated it, so its numbers change only when its owner moves the
tag. `packages/quant-toolbox/tests/api.lock.json` records every public name and signature, and a change fails the test
until the lock is rewritten and [CHANGELOG.md](../../packages/quant-toolbox/CHANGELOG.md) says what changed. While the toolbox is
0.x, a removed or changed name is a minor version bump; after 1.0, a major one.

To take a newer toolbox in a generated project, edit the `rev` under `[tool.uv.sources]` in its
`pyproject.toml`, then run `uv sync` and its tests.
