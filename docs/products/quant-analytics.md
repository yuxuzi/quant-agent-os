# Quant Analytics

**Quant Analytics** registers performance and risk metrics as Quant-Agent-OS tools, so a portfolio workflow
can compute them on a firm's own datasets by reference. It imports no LLM library. The numbers come from
[Quant Toolbox](quant-toolbox.md), the library the generated applications use too, so a tool call and a
generated report compute the same way ([decision 0026](../decisions/0026-quant-toolbox.md)):

- **Performance** (`quant_toolbox.performance`): annualised return and volatility, Sharpe, Sortino, Calmar,
  Omega, maximum drawdown, tail ratio, stability, value at risk, and hit rate; against a benchmark, alpha,
  beta, active return, tracking error, information ratio, up and down capture, and correlation. The shape
  of the distribution (bias-adjusted skewness and excess kurtosis, best and worst period) and how far to
  trust the Sharpe ratio (its Mertens standard error, the probabilistic Sharpe ratio against 0, and the
  minimum track record at 95%) come with every series. They run through empyrical-reloaded when it is
  installed, as the `analytics` extra installs it ([decision 0010](../decisions/0010-analytics-on-empyrical.md)),
  and through the toolbox's numpy formulas otherwise; a test holds the two to 1e-12, and each output's
  `conventions["source"]` names which ran.
- **Risk** (`quant_toolbox.risk`), on numpy, because empyrical has none:
  - Euler volatility contributions (marginal, component, and percent), which add up to the
    portfolio's volatility;
  - OLS factor exposures;
  - each factor's share of the portfolio variance, with the specific share as the remainder;
  - the portfolio's 95% value at risk per period, Cornish-Fisher so that skew and fat tails deepen it,
    its Gaussian expected shortfall, and its concentration as a Herfindahl index and effective positions.
  - with daily, dated returns, today's weights over historical stress windows (`SCENARIOS`, conventional
    US equity episodes from 2007 to 2024), each compounded over the window and applied linearly;
  - with a vendor's factor model (loadings, factor covariance, specific variances), the volatility
    `B F B' + D` gives, split into factor and specific shares and asset contributions.
- **Signals and backtests** (`quant_toolbox.research`), on numpy: information coefficients, quantile
  returns, and backtests with a trade lag, rebalancing, drift, and costs on turnover.
- `quant_analytics.computed` and `quant_analytics.signals`: typed results over those, with conventions,
  warnings, and `computed` evidence. An undefined metric is `null`.
- `quant_analytics.tools`: the tools (`analytics_tools()`). Series come as values, or as
  references to dataset columns, which need `read_local_data`.
  - `analyze_performance` returns every metric of a series.
  - `analyze_risk` returns a portfolio's volatility, its contributions, its parametric tail and
    concentration, and, with factor returns, its exposures. Daily, dated returns add
    `historical_stress`, the covered scenarios only; when none is covered, it is `null` and
    `historical_stress_note` says why. With `factor_model`, it adds the volatility under a vendor's
    model.
  - `analyze_signal` returns a signal's mean IC and rank IC, the rank IC's IR and Newey-West t-statistic
    per horizon, quantile mean returns and the top-minus-bottom spread, and the signal's
    autocorrelation, with a `summary` first.
  - `run_backtest` returns a signal's simulated net returns as dollar-neutral or long-only weights:
    annualised return and volatility, Sharpe, maximum drawdown, hit rate, turnover, cost drag, the
    toolbox's analysis of the net returns, and, with a benchmark, alpha and beta. It is a simulation of
    past data, not a forecast or advice, and says so.
  - The signal tools take frames by reference only, so they are served only with a dataset store.
  - **Frequency:** `periods_per_year` is inferred from a dataset's dates. Inline values have no
    dates, so they must pass it, and a call with neither is refused.
  - **Alignment and gaps:** series from different datasets are joined on their dates, and rows
    with a missing value are left out. Both are counted in the warnings; nothing is filled in.

## See the result in your client

`analyze_performance` links a page, `ui://quant-agent-os/performance.html`, which a client that shows
MCP Apps draws in place of the result's text: the series and its periods, the conventions, a metrics
table with each value formatted as its kind (percentages, counts, and ratios), the warnings, the
evidence, and the data versions. The page is served beside the tool, by `qaos serve`, the stdio
server, and `serve_apps`, and only where `analyze_performance` is served to the caller
([decision 0036](../decisions/0036-views-for-mcp-apps.md)). It makes no request and loads nothing,
and it draws only what the tool returned.

The MCP site listed these clients as supporting MCP Apps when this was written: Claude, Claude
Desktop, VS Code GitHub Copilot, Microsoft 365 Copilot, Goose, Postman, MCPJam, and Archestra.AI.
Any other client ignores the page and shows the same text result as before.

This has been tested by protocol tests (`tests/contract/test_mcp_views.py`, which check the metadata,
the page resource, and the fallback over both MCP eras) and by rendering the page in a headless browser
with a fake host. It has not yet been seen in a real client, so a first look in one is still to do.

## The shapes the datasets take

Every dataset is wide: an index column first, then one numeric column per series.

| Input | Rows (index) | Columns |
| --- | --- | --- |
| `asset_returns`, `factor_returns`, `benchmark` | dates, oldest first | one per asset, factor, or benchmark |
| `analyze_signal` and `run_backtest`: `signal`, `returns` | dates, oldest first | one per instrument, named the same in both frames |
| `factor_model.loadings` | asset names, as in `weights` | one per factor |
| `factor_model.factor_covariance` | factor names | the same factors, in any order |
| `factor_model.specific_variance` | asset names | the column named in the reference |

- **Signal and returns frames** are joined on their shared dates and their shared column names;
  what either leaves out is counted in the warnings. Row t of the returns is the simple return over the
  bar ending on date t. The signal on date t is known at that close and traded `lag` bars later (one
  by default), so `analyze_signal` scores it against the return from bar t + lag and `run_backtest`
  first holds it over bar t + 1 + lag. A missing signal leaves the instrument out on that date, and a
  date with no signal at all keeps the previous date's in a backtest. A missing return is no move and
  no trade. Returns that stop before the last date are a delisting and earn `delisting_return` (-30%
  by default, Shumway 1997) once; pass 0 when they are not.
- **A factor model** is annualised unless its `periods_per_year` says otherwise, when its covariances
  and specific variances are scaled up by that number. A missing loading counts as 0; a weighted asset
  with no row in the loadings or specific variances, a covariance that is not square or symmetric,
  or a loading on a factor with no covariance is refused. A dataset needs at least two rows, so a
  one-factor model cannot be a dataset.

```python
from quant_agent_os.sdk import Platform
from quant_analytics.tools import analytics_tools

with Platform(analytics_tools()) as platform:
    record = platform.call(
        "analyze_risk",
        portfolio_id="book",
        weights={"stocks": 0.6, "bonds": 0.4},
        asset_returns={"stocks": [0.02, -0.03, 0.01, 0.04], "bonds": [0.004, 0.006, -0.002, 0.001]},
        factor_returns={"market": [0.015, -0.025, 0.012, 0.03]},
        periods_per_year=12,
    )
    print(record.output["contributions"])  # each asset's share of the volatility
```
