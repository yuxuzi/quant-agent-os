# Quant review: flaws, unnecessary complexity, clumsy UX, and the plan to fix them

**Scope.** I reviewed the quant baseline templates and App Factory (B-23 to B-26) as a quant
researcher would use them: generate a project, load prices, study a signal, backtest it, and
report it. I read the generated code and measured the suspected flaws on synthetic data.

**Verdict.** The software engineering is sound (tests, types, provenance, and secrets
handling), but three quant-level errors make the results untrustworthy. In order of damage:
1. The study and the backtest use different timing.
2. Delisted stocks lose nothing.
3. Costs ignore drift.

On top of that, the workflow is split across three projects, and the tooling asks for more
decisions than a quant should need to make. The plan below fixes correctness first, then
simplifies, then smooths the UX. Every step has a test that proves it.

## Findings

### A. Correctness (results are wrong or misleading)

| # | Flaw | Evidence (measured 2026-10-31) | Why it matters |
| --- | --- | --- | --- |
| A1 | **Study and backtest disagree on timing.** The study scores a signal against the return from t to t+1, as if trading at the signal's own close. The backtest's default trades one bar later. | A planted signal that predicts only the next bar: backtest Sharpe **+29.1** at lag 0, **−0.6** at the default lag 1. The study reports the lag-0 view. | A researcher promotes signals that the default backtest, and reality, cannot trade. This is the most damaging flaw. |
| A2 | **Delisted stocks lose nothing.** A missing return becomes 0 (`nan_to_num`), so a position whose prices stop simply stops earning. | A held stock with no final price earns **0** on and after the bar it vanished; the loss is dropped. With a final crash price, the loss is counted correctly. | This is survivorship bias inside the engine. Real delistings (bankruptcies, mergers) often have no final print. |
| A3 | **Costs ignore drift.** Turnover is computed on target weights, but holdings drift between bars. | Constant targets: modelled turnover **0**, actual rebalancing **0.77% per bar**. Costs are understated by **0.19% a year** at 10 bps, more for volatile or high-turnover books. | Net returns are flattered, worst for exactly the strategies that need costs modelled. |
| A4 | **Daily rebalancing only.** Weights are re-targeted every bar; there is no rebalance schedule. | A 20-bar signal is traded daily, at an average turnover of about 16% a bar in the demo. | Unrealistic turnover and costs. Most equity signals rebalance weekly or monthly. |
| A5 | **The t-statistics are overstated.** The rank-IC t-stat assumes independent bars, and IC decay at horizon h > 1 uses overlapping windows. | By construction: overlapping windows are serially correlated. | Significance is overstated, and multi-horizon decay looks smoother than it is. |
| A6 | **Dates are compared as strings.** The loader checks order lexically and never parses dates. | `1/10/2024` sorts before `1/9/2024`, so a valid US-format file is rejected as unordered, with a confusing message. | Real files fail, or a wrong order slips through in other formats. |
| A7 | **Signal defaults are not the standard definitions.** "Momentum" is 20 bars with a 1-bar skip, and "reversal" is 1 bar. | `signals.py` defaults. | Academic momentum is 12-1 months, and short-term reversal is about one month. The names promise something the defaults do not deliver. |

### B. Methodology gaps (missing pieces a quant expects)

- **B1. Walk-forward splits are never used.** `walk_forward_splits` exists, and `LIMITATIONS.md`
  tells users to choose parameters on training ranges, but nothing runs out of sample. Every
  number is in-sample.
- **B2. Exposures are not controlled.** The book is dollar-neutral but not beta- or
  sector-neutral, so results can be a market bet in disguise. There is no long-only mode with
  a benchmark, information ratio, or tracking error.
- **B3. Experiments are not reproducible.** Each run overwrites `results/results.json`, and a
  result records neither the data fingerprint nor the code version. That is ironic for a
  platform built on provenance.
- **B4. Reports are thin.** There is no equity curve, calendar-year table, benchmark line, or
  comparison of runs.

### C. Unnecessary complexity

- **C1. Three projects for one workflow.** Study, backtest, and report are separate generated
  projects. The researcher copies signal code between them, keeps three environments, and
  points the report at another project's file. The report profile is about 80 lines of code,
  far too little to justify a separate project.
- **C2. Three ways to say what to build:** `kind`, or `template` with `profile`, or a
  registered name. Specs also mix `answers`, `settings`, `connection`, and `components`.
- **C3. Metrics are copied into every project.** Each generated project carries its own copy
  of the metric code, so a fix reaches only new projects. The planned analytics package (B-13)
  would make a third copy.
- **C4. The approval ceremony is visible where it adds nothing.** A person running
  `quant-factory create` in their own terminal is asked to confirm, then the run is recorded
  as approve-and-resume. The ceremony matters for agents, not for an operator's own command.

### D. Clumsy UX

- **D1. Questions that do not apply.** `quant-factory new` asks for `DATABASE_PASSWORD` and
  `DATABASE_URL` variables for SQLite, then lists them as "set at runtime" noise.
- **D2. Too many first-time decisions.** There are about ten prompts before anything exists,
  components are typed as comma-separated text, and there is no default project name.
- **D3. Flag soup in generated CLIs.** Commands like
  `alpha-lab --database --signal reversal --lookback 5 --out results/study.json` have no
  subcommands, and parameters are split between flags and `config.py`.
- **D4. Agent tools take prices inline.** `evaluate_signal(prices=[[...]])` cannot carry a
  real panel, because inputs and outputs are size-limited. Tools should read the project's
  configured data.
- **D5. Verbose output.** Every `plan` prints the planner's reasons and the full file list.

## Plan

Each step is one commit with tests, and each keeps every quality gate passing. Phase 1 must
land before any design partner sees the templates, because it changes the numbers they would
see.

### Phase 1: correct numbers (B-27)

| Step | Change | Test that proves it |
| --- | --- | --- |
| **R1** One timing convention | Study, IC decay, quantile spreads, and backtest share `config.TRADE_LAG`. Forward returns start at t + 1 + lag, and the results file records the lag. | The planted next-bar signal shows strong IC at lag 0 and near-zero IC at lag 1, matching the backtest's Sharpe at each lag. |
| **R2** Missing data and delistings | A held position whose price disappears exits at a configured `DELISTING_RETURN` (default −30%, after Shumway 1997; set to 0 to opt out). Gaps inside a price series are bridged when prices resume. The results file counts the forced exits. | Known delisting: the loss is charged once. A one-bar gap: the return over the gap equals the price ratio. |
| **R3** Drifting holdings and a rebalance schedule | Holdings drift with returns between rebalances. Trades are target minus drifted weights, and costs are charged on actual trades. `REBALANCE_EVERY` defaults to 21 bars for daily data. | Constant targets give turnover equal to the drift correction (a known value on a two-asset path). Monthly rebalancing cuts turnover against daily. The no-look-ahead invariant still holds. |
| **R4** Honest significance | Mean IC t-stats use Newey-West (HAC) standard errors, with the lag set to at least the horizon minus 1. IC decay reports its HAC t at each horizon. | On an AR(1) IC series with a known autocorrelation, the HAC t matches its closed form and is below the naive t. |
| **R5** Real dates | Loaders parse ISO dates (CSV, Parquet, database) into `datetime.date`, and reject other formats with an example of the expected one. | `2024-01-09` sorts before `2024-01-10`. `1/9/2024` is rejected with a message naming ISO dates. |
| **R6** Standard signal definitions | `momentum(lookback, skip)` defaults to 12-1 (252 and 21 bars daily, scaled by frequency), and `reversal` defaults to 21 bars. Results record the parameters. | Known-answer tests on a constructed price path, at each frequency. |

**R1 result (2026-11-01).** Done as planned. The evaluation functions take `lag` (default
`config.TRADE_LAG`), and forward returns run from bar t + lag. On the planted next-bar world,
both profiles now agree:

| | lag 0 | lag 1 (default) |
| --- | --- | --- |
| Study rank IC | 0.268 | 0.009 |
| Backtest Sharpe | 23.9 | 1.0 |

A signal whose edge lasts past the lag (`generate_panel(delay=TRADE_LAG)`) keeps it: rank IC
0.275 and Sharpe 24.4. Both generated test suites assert this, and the study's CLI gained
`--lag`. One deviation: the study and the backtest are still separate projects, so "matching"
is asserted by the same planted world in each project's tests, not by one test across both.
S1 makes it a single test.

**R2 result.** Done as planned. `metrics.bar_returns` is the one definition of asset
returns: 0 inside a gap, the whole move when prices resume, and `DELISTING_RETURN` on the bar
after an asset's final price (only when that comes before the panel's end). The backtest
trades only where a price exists, so positions are carried through gaps, and a delisted
position is written off without a trading cost. Study forward returns compound the same
returns, so a stock delisted inside the horizon counts with its loss. With no missing prices,
held weights and turnover are unchanged. Deciding that prices have stopped for good uses the
rest of the panel; that matches how delisting returns are recorded (at the delisting date),
and it is documented in the code.

**R3 result.** Done as planned, in the same bar loop as R2. The schedule counts from the first
close with a position to take, so a warm-up period does not delay the first trade by up to a
month. On the demo panel (momentum, 20 bars), average turnover falls from 16.3% a bar
(daily) to 3.2% (every 21 bars). A constant-target book now trades 0.47% a bar of drift, or
0.12% a year at 10 bps, where it was modelled as zero. A random walk across 40 seeds still
shows no edge: mean gross return -0.40% a year, standard error 0.34%.

**R4 result.** Done as planned. `metrics.newey_west_t_stat` (Bartlett kernel, Newey-West
1994 lag rule) is checked against a hand-computed series and against the AR(1) closed form
(HAC t about 0.58 of the naive t at phi = 0.5). IC t-statistics use at least horizon - 1
lags. On a random walk, where no signal has an edge, 20-bar momentum had a naive rank-IC t of
-4.6 at a 5-bar horizon; the HAC t is -2.5, and -1.8 at 10 bars. The `ic_decay` series
became `ic_decay_mean_rank_ic` and `ic_decay_t_stat`.

**R5 result.** Done as planned. `data.parse_date` accepts `date` values, midnight `datetime`
values (as DuckDB and PostgreSQL return them), and ISO text, and refuses the rest, including
`1/9/2024`, `20240102`, impossible dates, and times of day. Panels hold `datetime.date` and
reject duplicate dates. The database smoke test had been inserting dates like `2024-0000`,
which only worked because nothing parsed them; it now uses real dates. The inline price tools
take optional `dates` and warn when bars are labelled with stand-in weekdays.

**R6 result.** Done as planned. `config` derives the defaults from the frequency
(`MOMENTUM_LOOKBACK = PERIODS_PER_YEAR`, and `MOMENTUM_SKIP`, `REVERSAL_LOOKBACK`, and
`REBALANCE_EVERY` all equal `PERIODS_PER_YEAR // 12`), so a weekly project gets 52-4 momentum
without its own table. Known-answer tests on a +1%-a-bar path check the formulas, and daily,
weekly, and monthly projects pass their own tests. Asking reversal for a `skip` is refused,
and the CLI turns bad parameters and data into usage errors.

**Exit:** a template correctness suite of synthetic worlds with known answers covers planted
next-bar and multi-bar signals, gaps, delistings, a random walk across 40 seeds (mean Sharpe
consistent with zero), and constant targets with drift. Every generated profile passes it in
the smoke test.

**Exit result (2026-11-01).** Met. Every generated project now tests these synthetic worlds
with known answers:
- planted next-bar and tradable signals (study and backtest agree at each lag);
- gaps and delistings (bar returns, backtest positions, and study forward returns);
- constant targets with drift (exact turnover), and the rebalance schedule;
- an AR(1) series for the HAC t-statistic;
- a random walk across 40 seeds, with no edge in the study's IC or the backtest's returns.

The smoke suite builds 9 projects, including a weekly backtest, and all pass their own
`uv sync`, ruff, mypy, and pytest.

### Phase 2: one project, honest out-of-sample results (B-28)

| Step | Change | Test that proves it |
| --- | --- | --- |
| **S1** One `quant-research` profile | One generated project has subcommands `study`, `backtest`, `walk-forward`, and `report`, sharing data, signals, metrics, and one `config.toml` for parameters. The `alpha-research`, `backtest`, and `report` kinds become aliases that build it, for one release. | Each subcommand runs on the demo panel. The old kinds build the same project and warn that they are aliases. |
| **S2** Experiment folders | Each run writes `results/<timestamp>-<label>/`, holding its parameters, data fingerprint (SHA-256 of the panel), code version (`git describe` when available), metrics, and series. | Two runs make two folders. Changing one price changes the fingerprint. |
| **S3** Walk-forward that is used | `walk-forward` picks each fold's parameters on the training range from a small grid, by IC or Sharpe, and reports out-of-sample results only. | Changing the data in any test window never changes the parameters chosen for that fold. |
| **S4** Exposure modes | The modes are dollar-neutral (default), beta-neutral against an equal-weight market, and long-only against a benchmark, with information ratio and tracking error. | Known answers: the beta-neutral book has an ex-post beta of about 0 on a planted market factor, and IR and TE match hand calculations. |
| **S5** A report that compares runs | `report` renders one or more experiment folders: an equity curve (inline SVG, no new dependency), a calendar-year table, and parameters side by side. Facts and interpretation stay apart. | A golden-file test checks the Markdown and SVG structure for two runs. |
| **S6** Tools that read the project's data | `agent-tools` wrappers take a signal and parameters, and read the configured data source (file or database), not inline prices. | `check_tool` passes, and the tool works on the demo panel and on a SQLite fixture. |

**S1 result (2026-11-01).** Done, with one deviation. The `quant-baseline` pack (now 2.0.0)
has one profile, `quant-research`. Its project has `study`, `backtest`, and `report`
subcommands, plus `check-connection` with the database component. All of them share data,
signals, metrics, and the trade lag. Research parameters (signal, lookback, skip, lag,
quantiles, IC-decay horizon, costs, rebalance schedule, and delisting return) live in a
generated `config.toml`. `parameters.py` loads it, refuses unknown keys and wrong types by
name, and lets flags override it for one run. A test checks that the generated file equals
the defaults in `config.py`.
- **Aliases.** The old kinds and profile names (`alpha-research`, `backtest`, and `report`, in
  specs, `new --profile`, `describe_template`, and `template fork`) build `quant-research`. They
  say so in the plan's reasons and in create's warnings.
- **Removed prompt.** `new` no longer asks for a profile when a template has only one.
- **One timing test.** The study-and-backtest agreement is now a single test (`test_timing.py`).
- **Deviation.** `walk-forward` is not a subcommand yet. A placeholder would have been a
  command that does nothing, so it arrives with S3, which makes it real.

The smoke suite builds `quant-research` four ways (default, weekly, monthly, and Parquet with
agent tools), with a database, and as a fork. All pass their own checks. Walkthrough §11 was
re-run as one project, with the same numbers as B-27.

**S2 result.** Done as planned, plus a parameters snapshot. Each `study` or `backtest` run
writes `results/<UTC time>-<kind>-<signal>/` (`--label` names it). A second run in the same
second gets a `-2` suffix, so no run overwrites another. Each folder holds two files:
- `results.json` (schema version 2): the metrics, series, and parameters, plus provenance:
  - `sha256:` of the panel's dates, assets, and prices, with one canonical NaN;
  - the package version and `git describe --always --dirty` when the project is a git checkout;
  - the UTC time.
- `config.toml`: the exact parameters, so `--config results/<run>/config.toml` repeats the run.
  A test checks the repeat gives identical metrics.

`report` with no argument renders the latest run, takes a run folder or a version-1 results
file, and shows the provenance. Each run also prints a one-line headline: rank IC and its HAC t
for a study, and net Sharpe, return, and turnover for a backtest. One bug was found by the
generated tests: two runs in the same second sorted by label, so "latest" could pick the wrong
run. Ties now break by when the results file was written.

**S3 result.** Done as planned. `walk-forward` rolls `train_bars` (one year by default) and
`test_bars` (three months) windows after a warm-up. Each fold picks the lookback with the best
training net Sharpe, or rank IC with `objective = "ic"`, from a small grid: `lookbacks`, or 0.5,
0.75, and 1 times the standard. The choice uses only prices up to the training window's end.
Only stitched test windows are reported, with a folds table in the report. Three tests prove it:
- **No leakage.** Randomising every price from a test window on never changes the choices of
  that fold or earlier ones, for both objectives.
- **Known answer.** In a world where returns undo the last five bars' move, the walk-forward
  chooses 5 from {2, 5, 10} in all 11 folds. The edge holds out of sample, with a Sharpe of 21
  when rebalancing daily.
- **Too little data.** A short panel is explained, not crashed.

Writing that test found a real interaction: with the monthly default rebalance, a two-bar edge
is gone before the book trades, and the choice drifts to longer lookbacks. The test rebalances
daily and says why.

**S4 result.** Done as planned. `exposure` in `config.toml` (or `--exposure`) chooses the book:
- **`dollar-neutral`.** Ranks, as before.
- **`beta-neutral`.** Rank weights projected onto zero net dollars and zero beta, using rolling
  betas to the equal-weight market from the `beta_window` bars up to each close.
- **`long-only`.** The top signal quantile, equal-weighted, with active return, tracking error,
  and information ratio against the equal-weight market. Its headline leads with the IR.

Every backtest and walk-forward reports its realised beta. Known answers:
- **Planted factor world.** Ranking on a beta-like signal leaves a dollar-neutral book with a
  realised beta of 0.40; the beta-neutral book has 0.02.
- **Exact neutrality.** Beta-neutral weights are exactly neutral, to 1e-10, in dollars and
  beta.
- **Rolling betas.** They recover the planted betas and never change when later prices do.
- **Formulas.** IR, TE, active return, and beta match hand calculations.

**S5 result.** Done as planned, with the chart written as a file. `report` takes nothing (the
latest run), one run, or several:
- **One run.** The report gains a calendar-year table and an equity curve.
- **Several runs.** `results/comparison.md` puts runs, parameters, metrics, and calendar years
  side by side, with one chart. It notes when the runs used different data, merges their
  limitations, and keeps the interpretation section empty.

The SVG is hand-written: no plotting dependency, break-even line, one labelled curve per run,
and dates on the axis. It is a separate `*-equity.svg` file next to the report, not inline,
because Markdown viewers such as GitHub strip inline SVG. Run names drop their timestamps when
the labels alone tell the runs apart. Structural golden tests cover the comparison's sections,
rows, and fingerprint note, the chart's polylines and legend, and compounded calendar years.

### Phase 3: less to decide, less to read (B-29)

| Step | Change | Test that proves it |
| --- | --- | --- |
| **U1** A one-command start | `quant-factory new quant-research my-lab --defaults` writes the spec and creates the project in one step. The project name comes from the argument or the directory. | A scripted run creates a passing project, with no prompts. |
| **U2** Questions that apply | Secret settings are asked only when the chosen options use them (no password for SQLite or DuckDB). Components are a numbered menu. There is no "set at runtime" noise. | Scripted `new` runs for each database kind assert which prompts appear. |
| **U3** Quiet by default | `plan` and `create` print a summary: the profile, the number of files, and the checks. Reasons and file lists move behind `--verbose`. | CLI output tests for both modes. |
| **U4** One way to name a target | Docs and `new` use `template` and `profile` only. `kind` is kept as a documented alias. | Every doc example validates, through a test that loads the specs in the docs. |
| **U5** Operator commands without ceremony | An operator's own `create` and `check` record a standing operator approval (`approved_by = cli:<user>`), without the approve-and-resume dance in the code path. Agent calls still need per-call approval. | Run records show the operator approval, and the agent path is unchanged (the existing approval tests). |

### Later, when Phase 1 and 2 have users

- **The metrics library (B-13).** The templates depend on a small platform-independent
  analytics package rather than copying metric code. This fixes C3 once there are users to
  migrate.
- **From research to spec (B-17).** Quant Research evidence (for example, "reversal relates to
  liquidity provision") seeds a study spec's signal and parameters, which is the bridge that
  makes the two products one platform.

## Out of scope

- Point-in-time fundamentals.
- Borrow costs and short-sale constraints beyond the documented limitation.
- Intraday data.
- Portfolio optimisers.
- Live trading or any broker connectivity (unchanged).
