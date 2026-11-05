# quant-agent-os-analytics

**Quant Analytics** holds performance and risk metrics with documented conventions, and
registers them as Quant-Agent-OS tools. It imports no LLM library. The performance formulas
are empyrical-reloaded's ([decision 0010](../../docs/decisions/0010-analytics-on-empyrical.md)).

- `quant_analytics.metrics`: plain functions on empyrical. They cover annualised return and
  volatility, Sharpe, Sortino, Calmar, Omega, maximum drawdown, tail ratio, stability, value
  at risk, and hit rate. Against a benchmark, they add alpha, beta, active return, tracking
  error, and information ratio. The conventions are in the module docstring.
- `quant_analytics.risk`: portfolio risk on numpy, because empyrical has none:
  - Euler volatility contributions (marginal, component, and percent), which add up to the
    portfolio's volatility;
  - OLS factor exposures;
  - each factor's share of the portfolio variance, with the specific share as the remainder.
- `quant_analytics.computed`: typed results over those two, with conventions, warnings, and
  `computed` evidence.
- `quant_analytics.tools`: the tools (`analytics_tools()`). Series come as values, or as
  references to dataset columns, which need `read_local_data`.
  - `analyze_performance` returns every metric of a series.
  - `analyze_risk` returns a portfolio's volatility, its contributions, and, with factor
    returns, its exposures.
  - **Frequency:** `periods_per_year` is inferred from a dataset's dates. Inline values have no
    dates, so they must pass it, and a call with neither is refused.
  - **Alignment and gaps:** series from different datasets are joined on their dates, and rows
    with a missing value are left out. Both are counted in the warnings; nothing is filled in.

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
