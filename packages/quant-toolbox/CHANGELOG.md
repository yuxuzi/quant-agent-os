# Changelog

Quant Toolbox's own changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the package uses [Semantic Versioning](https://semver.org/): while it is 0.x, a minor release may change its
public API, always with an entry here and a rewritten `tests/api.lock.json`.

## [Unreleased]

### Changed

- `data.excel.read_workbook` says what it read when no data follows the header row: the row it read as
  the names, its cells, the row below it, and the first date's row. It said "no rows of data below row
  N", which did not show a blank row or a text row as the cause.

## [0.8.0] - 2026-11-19

Released with Quant-Agent-OS 0.9.0.

### Added

- **Trading calendars** (`quant_toolbox.data`, numpy only; decision 0033).
  `as_calendar(source, first, last, holidays=, open_days=)` reads an `exchange_calendars` or
  `pandas_market_calendars` calendar, a pandas `CustomBusinessDay` or holiday calendar (such as
  `USFederalHolidayCalendar`), a `numpy.busdaycalendar`, any object with `name`, `first`, `last`, and
  `sessions(start, end)` (the `Calendar` protocol), or nothing (weekdays), by duck typing, so none of those
  libraries is a dependency. `holidays` adds ad hoc closures to the source's own, and `open_days` overrides
  the source, so US federal rules plus Good Friday, less Columbus and Veterans Day, give NYSE's 252 sessions
  of 2024. The result, a `SessionCalendar`, knows its days from `first` to `last` and raises `CalendarError`
  outside them rather than guessing; it has `sessions`, `count`, `is_session`, `mask`, `offset`, `roll` (the
  ISDA following, preceding, and modified conventions), `sessions_from`, `period_ends` (last session of each week, month,
  quarter, or year), and `sessions_per_year`. `joint` and `either` combine calendars. Its name records the
  caller's edits, for a report to state. A firm may subclass it, and `as_calendar` returns the subclass as is.
- **A default calendar and alignment to it.** `set_default_calendar` sets the fund's calendar once for the
  process, and `using_calendar` another for one `with` block (per thread or task), for a fund on several
  exchanges; with neither, `default_calendar()` is weekdays, named `"weekdays (no holidays)"`, and
  `configured_calendar()` is None, so a check that would be noise against weekdays can skip.
  `align_to_calendar(table, calendar=None, fill=, off_session=)` gives a table one row per session (NaN, or
  the previous value), and refuses a row on a non-session, naming it, unless told to drop it. `session_gaps`
  returns the sessions a table lacks and its rows on non-sessions.
- **Calendars in use.** `demo_panel`, `demo_returns`, and `demo_book` take `calendar=` and date their rows by its
  sessions, or by the default calendar when none is given (identical to weekdays until a calendar is set).
  `return_notes(table, calendar=)` notes rows on non-sessions and missing sessions when a calendar is given or
  configured, and is unchanged otherwise.
- **`run_backtest(rebalance_on=)`** rebalances on the closes a per-bar flag marks, such as month ends from a
  calendar, as well as on the first close with a position. It needs `rebalance_every` left at 1, and one
  flag per bar.

- **A firm's own metric set.** `analyze(metrics=)` and `tear_sheet(metrics=)` take any function with
  `all_metrics`'s signature (the `MetricsFunction` protocol); None, the default, is `all_metrics`. A set can extend the
  standard one by calling it, or replace it. Its result is checked to be numbers by name, `conventions["metrics"]`
  names a set other than the default, and the tear sheet shows the standard headline metrics the set has, then
  the rest.

- **A returns section, by period** (`report.returns_table`, `RETURN_PERIODS`): a row per series (a fund,
  its benchmarks, peers) and a column per period (`1D`, the return on the last date, then MTD, QTD, YTD,
  1Y, 3Y, 5Y, ITD, or any horizon), with the fund's excess over each other series in rows of its own.
- **A monthly factsheet** (`report.factsheet`), the common elements of UCITS and hedge-fund factsheets: fund
  facts, returns by period against the benchmark and peers, calendar-year returns (a table and a chart),
  growth of 1, risk measures over 1, 3, and 5 years, rolling volatility, top holdings, and a breakdown by
  group against the benchmark's, with a disclaimer as the footer. Only the fund's returns are needed; each
  other input adds its section. Its sections stand alone: `facts_table`, `calendar_year_table`,
  `calendar_year_chart`, `risk_measures_table` (return, volatility, Sharpe, Sortino, drawdown, and with a
  benchmark beta, alpha, correlation, R squared, tracking error, information ratio, and up and down
  capture, over the horizons of the returns table), `rolling_chart` (volatility or beta),
  `holdings_table`, `breakdown_table`, and `breakdown_chart`.
- **A risk report** (`report.risk_report`) of a book's weights and asset returns, laid out from
  `quant_toolbox.risk`: exposure and concentration, exposure by group, historical and parametric value at
  risk and expected shortfall, contribution to risk, historical stress, liquidity by bucket (with ADV and
  positions), factor risk (with a factor model), and correlation among the largest positions. Its
  sections stand alone too: `exposure_table`, `group_exposure_table`, `tail_risk_table`,
  `contribution_table`, `stress_table`, `liquidity_table`, `factor_table`, and `correlation_table`.
- **One call to write a report** (`report.write_report(document, path, charts=)`, `REPORT_FORMATS`): HTML,
  Markdown, JSON, PDF, or Excel by the file's suffix, creating folders; an unknown suffix, or interactive
  charts for a format other than HTML, is refused.
- **Shaded tables and a footer.** `TableBlock(shade=True)` colours numbers by sign in HTML and PDF (gains
  green, losses red), and `Document(footer=)` (a disclaimer, say) follows every block in every format.
- **PDF reports** (`quant_toolbox.report.pdf`, the new `pdf` extra: fpdf2, pure Python). `render_pdf(document)`
  and `write_pdf(document, path)` draw any `Document` on A4: tables, and charts drawn as vectors from their
  data. Without the extra, the call raises `MissingExtraError` naming `uv add "quant-toolbox[pdf]"`.

### Changed

- **The tear sheet's returns table** compares the fund with its benchmark and with each series in the
  new `compare=`, adds excess rows and a `1D` column, and takes `periods=`; it was one row of the fund's
  horizons.
- Horizons (`horizon_return`, `analyze(calendar=)`) use a calendar's month-end sessions and session
  coverage when a calendar is given or configured, and are unchanged otherwise. `analyze` records the
  calendar's name in `conventions`, and `tear_sheet` passes its `calendar=` through.
- **Calendars in use.** `demo_panel`, `demo_returns`, and `demo_book` take `calendar=` and date their rows by its
  sessions, or by the default calendar when none is given (identical to weekdays until a calendar is set).
  `return_notes(table, calendar=)` notes rows on non-sessions and missing sessions when a calendar is given or
  configured, and is unchanged otherwise.

### Fixed

- **`run_backtest` refuses a missing weight.** A NaN or infinite weight made that bar's return and
  turnover NaN, then wrote the whole book off and charged a phantom rebuy on the next rebalance. It now
  raises `ValueError` naming the first bar and asset column, and asks for 0 for no position. Prices and
  weights of different shapes raise a `ValueError` naming both shapes, where numpy's broadcast error did.
- The factsheet's risk measures note says how capture is measured (arithmetic, per bar), so it is not
  read as Morningstar's monthly geometric capture.

## [0.7.0] - 2026-11-19

Released with Quant-Agent-OS 0.8.0.

### Added

- **A standard SQL connection** (`quant_toolbox.data.sql`, the new `sql` extra: SQLAlchemy 2, loaded in the function
  that needs it). `connect(url, credential=, read_only=)` makes an engine, with a credential callable run for every
  new connection so an RDS IAM or Entra token never goes stale, and a best-effort read-only session for SQLite,
  PostgreSQL (psycopg), and a DuckDB file (a warning for any other dialect, never silence). `fetch_rows(engine, sql,
  params)` runs SQL with `:name` binds on any driver and returns `Rows` with `Decimal` as float, dates as ISO text,
  NULL as NaN in a column of numbers, and lower-case column names (Snowflake's upper case reads as everyone's).
  `pivot_long` turns `(index, column, value)` rows wide and refuses two rows for one pair, naming them, unless told
  `duplicates="sum"` or `"last"`: a call and a distribution on one day, netted, is a silent error. `SqlDataset`
  declares a dataset as data (with `index_name` to rename the index column), `shape_rows` shapes its rows,
  and `rows_to_csv` writes them. The database's own driver is the caller's install.

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
