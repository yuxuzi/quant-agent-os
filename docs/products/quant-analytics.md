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
- `quant_analytics.computed`: typed results over those, with conventions, warnings, and
  `computed` evidence. An undefined metric is `null`.
- `quant_analytics.tools`: the tools (`analytics_tools()`). Series come as values, or as
  references to dataset columns, which need `read_local_data`.
  - `analyze_performance` returns every metric of a series.
  - `analyze_risk` returns a portfolio's volatility, its contributions, its parametric tail and
    concentration, and, with factor returns, its exposures.
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
