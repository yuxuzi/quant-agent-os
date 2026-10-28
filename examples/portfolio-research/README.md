# Portfolio research (offline example)

This example shows the whole MVP workflow shape on synthetic data:

1. A request and data come from `sample_config.toml`.
2. A fake provider returns a scripted plan (no LLM, no network).
3. The orchestrator validates the plan against the registry and step limit.
4. Two deterministic tools compute annualized return and maximum drawdown.
5. A report is rendered with findings, sources, warnings, limitations, and provenance.

```bash
uv run python examples/portfolio-research/portfolio_research.py
```

Try a series with fewer than 12 returns to see the insufficient-data warnings, or put an
unknown tool in `metrics` to see the plan rejected before anything runs.

The tools in `portfolio_tools.py` stand in for the standalone portfolio analytics package. In
QuantFoundry, analytics live in their own package, and QuantAgentOS registers them as tools.
