# quant-agent-os-analytics

**Quant Analytics** holds performance metrics with documented conventions, and registers them as
Quant-Agent-OS tools. It imports no LLM library and no numpy.

- `quant_analytics.metrics`: plain functions. They cover annualised return and volatility,
  Sharpe, maximum drawdown, hit rate, beta, active return, tracking error, and information
  ratio. The conventions are in the module docstring, and they match the generated
  `quant-research` projects.
- `quant_analytics.tools`: `analyze_performance` returns every metric of a series in one
  read-only call. With a benchmark it adds the benchmark metrics. The result carries the
  conventions, short-sample warnings, and `computed` evidence. `annualized_return` and
  `max_drawdown` are single-metric tools.

```python
from quant_agent_os.sdk import Platform
from quant_analytics.tools import analytics_tools

with Platform(analytics_tools()) as platform:
    record = platform.call(
        "analyze_performance", series_id="fund", returns=[0.01, -0.02, 0.015], periods_per_year=12
    )
```
