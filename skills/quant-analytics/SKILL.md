---
name: quant-analytics
description: Compute performance, risk, signal, and backtest metrics (Sharpe, drawdown, risk contributions, stress, IC, simulated net returns) through the Quant-Agent-OS MCP server, passing data by reference. Use when the user asks how a fund, strategy, or portfolio performed, what drives its risk, or whether a signal works.
license: MIT
compatibility: Needs the Quant-Agent-OS MCP server (`qaos serve`) with the analytics toolset.
metadata:
  min-server-version: "0.1.2"
---

# Performance and risk metrics

The server computes; you report. Numbers come from the tools with their conventions, never
from your own arithmetic.

## Find the data first

Call `list_datasets` to see the datasets the server can read, with their columns and rows.
Read one dataset's details with `dataset_id`, or the resource `qaos://dataset/{dataset_id}`.

Pass series **by reference**, never by copying values:
- **Performance:** `analyze_performance` with `returns` as `{"dataset": ID, "column": NAME}`,
  optionally a `benchmark` reference the same way. `risk_free` is an annual rate.
- **Risk:** `analyze_risk` with `weights` naming each asset, `asset_returns` as
  `{"dataset": ID}` (one column per asset, named like the weights), and, for exposures,
  `factor_returns` as `{"dataset": ID, "columns": [...]}`. For a vendor's model, pass
  `factor_model` with `loadings` and `factor_covariance` dataset ids and `specific_variance`
  as a column reference; set its `periods_per_year` when the model is not annualised. Daily
  dated returns also give `historical_stress`; when it is null, quote `historical_stress_note`.
- **Signals:** `analyze_signal` with `signal` and `returns` as `{"dataset": ID}`, both dates
  by instruments with the same column names. Quote its `summary` first: rank IC, IR, HAC
  t-statistic, and the top-minus-bottom spread.
- **Backtests:** `run_backtest` with the same frames, `cost_bps` (ask the user; never assume
  zero), and `weighting` ("dollar_neutral" or "long_only"). Keep the default `lag`.

Inline number lists are accepted only for small examples the user typed. If the data is not
there, the user adds it: `qaos data add FILE --id ID`. Adding data is never a
tool call.

**Leave `periods_per_year` out** when the data has dates: the server infers it. If it says the
dates show no frequency, ask the user (252 daily, 52 weekly, 12 monthly); do not guess.

## Report what was computed

- **Quote numbers as computed.** Never recompute, round differently, or "correct" a value in
  prose.
- **State the conventions** the result lists, for example a geometric annual return, sample
  volatility, or a drawdown as a negative fraction.
- **Pass on the warnings,** such as rows left out (missing values, aligning datasets), a
  frequency that disagrees with the dates, returns that look like percentages, a short sample,
  a series that does not vary, or collinear factors.
- **Explain risk contributions plainly.** Percent contributions sum to one, and a negative one
  means the position hedges the rest. Factor variance shares plus the specific share sum to
  one.
- **A backtest is a simulation, not advice.** Say so, with its period, costs, and lag; pass
  on look-ahead warnings and never present it as a track record.
- **Name the source.** Each figure is `computed` evidence; cite the dataset and periods it
  names.

## When a call fails

Failures carry `hint` and `retryable`. An `invalid_input` error names the field to fix. On
`denied` or `approval_required`, tell the user and do not retry.

## Related

The `analyze_returns` prompt runs the performance workflow in clients without skills. For a
report that combines research evidence with a series' metrics, use the `quant-strategy`
skill.
