# Quant Toolbox

Typed, numpy-first performance, risk, research, and report code for investment teams. Every convention is
stated, an undefined number is NaN with a note rather than a guess, and only numpy is required.

It is the library under [Quant-Agent-OS](https://github.com/yuxuzi/quant-agent-os)'s analytics tools and
under every application its App Factory generates, and it stands on its own: it imports nothing of the
platform. MIT licensed.

## Install

Releases are tagged in the Quant-Agent-OS repository; install with uv:

```bash
uv add "quant-toolbox[charts] @ git+https://github.com/yuxuzi/quant-agent-os@v0.6.0#subdirectory=packages/quant-toolbox"
```

| Extra | Adds | For |
| --- | --- | --- |
| (none) | numpy | every calculation |
| `charts` | pygal | static SVG charts in HTML reports |
| `interactive` | plotly | interactive charts |
| `excel` | openpyxl | reading and writing workbooks |
| `pandas` | pandas | `data.frames.to_pandas` and `from_pandas` |
| `empyrical` | empyrical-reloaded | computing the shared metrics through empyrical, held equal to numpy |
| `all` | all of the above | |

## Use

```python
from quant_toolbox.data import demo_returns
from quant_toolbox.performance import analyze
from quant_toolbox.report import render_html, tear_sheet

table = demo_returns()  # or quant_toolbox.data.load_table(Path("returns.csv"))
fund, index = table.column("Macro"), table.column("Index")

result = analyze(table.dates, fund, 252, benchmark=index)
print(result.metrics["sharpe_ratio"], result.benchmark["up_capture"], result.notes)
result.to_dict()  # JSON-safe: NaN is null, dates are ISO text

html = render_html(tear_sheet(table.dates, fund, 252, name="Macro", benchmark=index))
```

| Subpackage | Holds |
| --- | --- |
| `data` | tables and price panels with provenance files, configuration, data checks, synthetic data |
| `performance` | metrics, benchmark and capture statistics, rolling values, distribution, Sharpe inference (probabilistic, deflated, minimum track record), parametric VaR, Brinson attribution, horizons, drawdowns, `analyze` |
| `risk` | exposures and concentration, liquidity buckets, Euler and expected-shortfall contributions, EWMA covariance, factor exposures, a vendor factor model's risk, style regression, correlation, linear and historical-scenario stress |
| `research` | information coefficients, decay, quantile returns, signal hygiene (z-score, winsorize, neutralize), weights, the backtest engine with per-asset costs and cost drag, walk-forward and purged k-fold splits |
| `report` | reports as data, rendered to HTML, Markdown, JSON, or Excel; the investor tear sheet; charts |
| `audit` | provenance, code versions, run folders that are never reused |
| `testing` | one-line bias checks: no look-ahead, the future never changes the past, costs only cost, no edge on random walks |

Each subpackage imports only the ones above it in this table, and only their public names. The public API is
locked by `tests/api.lock.json`; a change to it is in [CHANGELOG.md](CHANGELOG.md).

## Test

```bash
cd packages/quant-toolbox
uv run --isolated --no-project --with . --with pytest python -m pytest
```

With numpy alone, tests that need an optional library skip. The repository's own `uv run pytest` runs them
with every extra and under both metrics backends.

More: the [product page](https://github.com/yuxuzi/quant-agent-os/blob/main/docs/products/quant-toolbox.md).
