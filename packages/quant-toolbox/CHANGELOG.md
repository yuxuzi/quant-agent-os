# Changelog

Quant Toolbox's own changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the package uses [Semantic Versioning](https://semver.org/): while it is 0.x, a minor release may change its
public API, always with an entry here and a rewritten `tests/api.lock.json`.

## [Unreleased]

## [0.6.0] - 2026-11-17

The first release as a package of its own. It adds what a fund's PM, risk manager, researcher, and investor
relations team ask of a performance and risk library, chosen from a review of the open-source libraries and
of what funds report, and keeps only numpy required.

### Added

- **One call for a return series.** `performance.analyze` gives the metrics, a benchmark comparison with capture
  ratios, the distribution, Sharpe inference, horizons, drawdowns, data notes, and conventions, as a typed
  `Analysis` whose `to_dict()` is JSON-safe (NaN becomes null, dates ISO).
- **An investor tear sheet.** `report.tear_sheet` turns a series, and optionally a benchmark, into a `Document`
  of headline metrics, horizons, the benchmark comparison, the monthly grid (`report.monthly_table`), the
  deepest drawdowns, the distribution and Sharpe confidence, three charts, and the conventions; it renders to
  HTML (static SVG, no script), Markdown, JSON, or Excel like any document.
- **Performance.**
  - `capture_ratios`: up and down capture, up and down beta, and correlation against a benchmark.
  - Rolling values: `rolling_volatility`, `rolling_sharpe`, `rolling_beta`, and `rolling_correlation`,
    vectorised, NaN until the window fills or over a gap, and annualised with periods per year, never the
    window length.
  - `return_distribution`, `skewness`, `excess_kurtosis`: bias-adjusted moments (scipy's `bias=False`), best
    and worst bars, average gain and loss, gain-loss ratio, and profit factor.
  - `parametric_var` (Gaussian or Cornish-Fisher, `VAR_METHODS`) and `parametric_expected_shortfall`, per bar
    and negative for a loss, like `value_at_risk`.
  - How far to trust a Sharpe ratio: `sharpe_standard_error` (Mertens), `probabilistic_sharpe_ratio`,
    `deflated_sharpe_ratio`, `expected_maximum_sharpe`, and `minimum_track_record` (Bailey and López de
    Prado). A test reproduces the 2014 paper's example, and probabilities stay within [0, 1].
  - `brinson`: single-period Brinson-Fachler attribution by group, whose effects sum exactly to the active
    return. Multi-period linking is not included.
  - `Drawdown.length` and `Drawdown.recovery_bars`, in bars.
- **Risk.**
  - `liquidity`: days to liquidate each position at a share of its average daily value traded (20% by
    default), and the share of gross exposure liquidated within Form PF-style horizons (`LIQUIDITY_BUCKETS`:
    1, 7, 30, 90, 180, 365 days, more, and no ADV).
  - `Exposures.herfindahl`: the sum of squared shares of gross; `effective_positions` is its reciprocal.
  - Historical scenarios: `SCENARIOS`, twelve conventional US equity stress windows from 2007 to 2024, with
    `scenario_shocks` and `historical_stress`, which apply a window's compounded asset returns to today's
    weights. A window needs daily data across it (no gap over `MAX_GAP_DAYS`, five days); otherwise the
    answer is None, never a shorter window under the scenario's name.
  - `factor_risk`: a vendor's factor model as input (loadings, factor covariance, specific variances), giving
    total, factor, and specific volatility, factor exposures, Euler factor shares, and asset contributions.
  - `style_regression`: returns-based alpha and betas against factor returns the caller supplies, with
    Newey-West t-statistics, R squared, and residual volatility.
  - `correlation_summary`, `expected_shortfall_contributions` (they sum exactly to the book's expected
    shortfall), `ewma_covariance` (RiskMetrics decay or a half-life), and `euler_contributions`, Euler
    volatility contributions from any covariance: sample, EWMA, or a vendor's. `MEASURE_CONVENTIONS` states
    the conventions of the new measures.
- **Research.**
  - Purged splits: `walk_forward_splits` takes `purge` and `embargo`, and `purged_kfold_splits` gives k
    contiguous test folds, each trained on every other bar less a purge before it and an embargo after it.
  - Per-asset costs: `run_backtest`'s `cost_bps` takes one rate or one per asset, and `BacktestResult.costs`
    records each bar's charge. `cost_drag` gives gross and net annualised return, the drag, annual turnover,
    the cost per unit of turnover, and the break-even cost in basis points.
  - `quantile_returns`: each signal quantile's mean return per bar; `quantile_spread` is built on it.
  - `run_backtest` refuses a cost rate that is not finite, naming the asset; a negative rate is a rebate.
  - `ic_summary` adds `rank_ic_std` and `rank_ic_ir` (per bar, not annualised).
  - Signal hygiene that never looks ahead: `zscore`, `winsorize`, `demean_by_group`, and `neutralize`.

### Changed

- **Quant Toolbox is its own distribution, `quant-toolbox`**, in `packages/quant-toolbox` of the Quant-Agent-OS
  repository, with its own version, changelog, and tests (decision 0027). It needs only numpy; its extras are
  `charts` (pygal), `interactive` (plotly), `excel` (openpyxl), `pandas`, `empyrical`, and `all`. A missing
  optional library's error names the quant-toolbox extra that brings it.
- **Faster research functions**, measured on a 2,520 by 500 panel: `information_coefficient` 60 to 6 ms
  (Pearson) and 169 to 94 ms (rank), `quantile_spread` 96 to 68 ms, `dollar_neutral_weights` 73 to 36 ms,
  `market_returns` 17 to 10 ms, `rolling_betas` 85 to 74 ms. Each matches the loop it replaced to within
  1e-12. A bar whose values are all equal now always has no information coefficient.

### Fixed

- `annualized_return` is NaN when wealth goes below zero, on both backends: the numpy backend raised on a
  complex number, and empyrical answered a meaningless large number.

## [0.5.0] - 2026-11-17

First release, inside the `quant-agent-os` 0.5.0 wheel. See that release's notes.
