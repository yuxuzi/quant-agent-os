# Portfolio research (offline example)

This example shows the whole workflow shape on synthetic data, with no model and no network:

1. A request and data come from `sample_config.toml`.
2. `Platform.report` runs the configured calls as one recorded run.
3. Two deterministic tools compute annualized return and maximum drawdown.
4. A report is rendered with findings, sources, warnings, limitations, and provenance.

```bash
uv run python examples/portfolio-research/portfolio_research.py
```

Try a series with fewer than 12 returns to see the insufficient-data warning, or put an unknown
tool in `metrics` to see it reported as a warning.

The tools in `portfolio_tools.py` are small on purpose. The platform's full analytics are
`quant_analytics.tools`.
