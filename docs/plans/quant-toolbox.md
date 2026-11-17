# Quant Toolbox: one library under Quant Analytics, the packs, and every generated application

Direction from the owner, 2026-11-17: the four `quant-apps` applications and `quant-lab` each carry their own
copy of the same tables, metrics, report, and provenance code. Put that code in one package, make it stable,
and make it the one implementation that Quant Analytics, the template packs, and the generated applications
all import, so a formula, a table reader, or a report renderer exists once. Generated applications then hold
only their own business logic. Static charts are the default; plotly and pandas are optional.

This plan follows [decision 0009](../decisions/0009-quant-baseline-templates.md), which foresaw it: "if the
analytics package ships, `metrics` may become a dependency on it rather than generated code. That would be a
later, deliberate change." This is that change. It amends decisions 0009, 0010, 0018, 0019, and 0022, as
decision 0026 (step 1.8) will record.

## 1. What is inconsistent today

Measured on `main` at 29e1c45 (2026-11-17). In the pack templates, in lines:

| File | Lines | Copies | Where | Held identical by |
| --- | --- | --- | --- | --- |
| `metrics.py` | 231 | 5 | every application and `quant-lab` | `test_quant_apps`, `test_metric_parity` |
| `tables.py` | 176 | 4 | every application | `test_quant_apps` |
| `document.py` | 256 | 2 | risk-monitor, performance-report | `test_quant_apps` |
| `report.py` | 229 | 3 | alpha-research, strategy-backtest, `quant-lab` | nothing |
| `results.py` | 123 | 3 | alpha-research, strategy-backtest, `quant-lab` | nothing |
| `evaluation.py` | 128 | 3 | alpha-research, strategy-backtest, `quant-lab` | nothing |
| `risk_metrics.py` | 53 | 2 | risk-monitor, performance-report | `test_quant_apps` |
| `synthetic.py` | 35 to 59 | 5 | every application and `quant-lab` | nothing (three variants) |
| `_git_describe` and run-folder helpers | about 40 | 5 | `results.py`, `monitor.py`, `reporting.py` | nothing |
| `backtest.py`, `walk_forward.py` | 249 to 300 | 2 | strategy-backtest, `quant-lab` | nothing (75 lines differ) |

Between Quant Analytics and the packs, the same work in different code:

| Quant Analytics | Lines | The packs' version | Held together by |
| --- | --- | --- | --- |
| `metrics.py` (through empyrical) | 136 | `metrics.py`, `risk_metrics.py` (numpy) | `test_metric_parity`, on nine metrics |
| `risk.py`: Euler contributions, factor exposures | 171 | risk-monitor's `risk.py`: contributions, exposures, stress | nothing |
| `computed.py`: `CONVENTIONS`, `RISK_CONVENTIONS` | about 40 | the conventions in each module's docstring, and `reporting.CONVENTIONS` | nothing |
| `computed.py`: short-sample and percent-like warnings | about 25 | performance-report's `check_data`: gaps, outliers | nothing |
| `computed.py`: `_label`, `_format`, `RATIOS` | about 20 | `report.py`'s `PERCENT_METRICS`, `format_value`; `document.py`'s `format_cell` | nothing |
| `tools.py`: aligning series on shared dates, counting what was left out | about 40 | risk-monitor aligning positions with prices; performance-report joining a benchmark | nothing |

On 2026-11-17 the packs' numpy Sortino, Calmar, and value at risk matched empyrical's to within 1e-15 on 504
daily returns; the other six already pass `test_metric_parity`. So one implementation can serve both.

What that costs:

- **A generated application is about half library code.** Of a performance-report's 2,075 lines, about 1,070
  are copies of code no firm should edit (tables, metrics, risk measures, the document model, horizons, Excel,
  synthetic data). A strategy-backtest is 2,067 lines, about 1,250 of them library code.
- **Fixes reach new projects only.** A metric fix lands in five template copies and in no generated project
  ([decision 0025](../decisions/0025-templates-are-starting-points.md) made that explicit). A library that a
  project pins can be upgraded by changing one line.
- **Two implementations of the same numbers.** The platform computes metrics through empyrical
  ([decision 0010](../decisions/0010-analytics-on-empyrical.md)) and the packs through numpy, with
  `tests/factory/test_metric_parity.py` holding nine of them together. Risk decomposition, conventions text,
  data warnings, and number formatting each exist twice with nothing holding them together, so an agent
  calling `analyze_risk` and a risk officer reading a risk-monitor report can see different definitions.
- **Charts are hand-written SVG,** once in `document.py` (a line chart) and once in `report.py` (an equity
  curve), with no bar, box, or heatmap, so a report section that wants one writes its own.
- **The generated tests test the library.** `test_metrics.py`, `test_horizons.py`, `test_results.py`, and
  parts of `test_risk.py` check code the firm did not write and should not change.

## 2. Design

### 2.1 Quant Toolbox is a sixth import name, under MIT

- **`src/quant_toolbox/`**, product name "Quant Toolbox", in the one wheel
  ([decision 0018](../decisions/0018-one-wheel.md)). It is a library, not a product: it registers no tools,
  serves nothing, and has no command.
- **MIT, like the packs.** Decision 0019's list of what stays MIT gains `src/quant_toolbox/`, and the wheel's
  `license-files` names its `LICENSE`. A generated project then runs MIT code only, from a wheel that also
  carries BUSL code it never imports. Counsel reviews this wording with the rest of 0019.
- **Why not a second wheel.** With no PyPI release (the owner's standing decision), a second distribution
  gives a firm no easier install: it would come from the same git tag, with a `subdirectory`. It would add a
  second version number to keep in step, which is what decision 0018 removed. The import boundaries below keep
  a later split possible, as 0018 says.
- **Dependencies by extra.** The base wheel stays pydantic, pydantic-settings, rich, and typer. Only numpy is
  required to import every toolbox module; each other library is optional and loaded by the one function that
  needs it.

  | Extra | Adds | Needed for | Measured (CPython 3.13, 2026-11-17) |
  | --- | --- | --- | --- |
  | `toolbox` | `numpy>=1.26`, `pygal>=3.1,<4` | everything, with static SVG charts | pygal 3.1.3 is 532 KB, pure Python, `py3-none-any`, two tiny deps, released 2026-06-18 |
  | `interactive` | `plotly>=7,<8` | interactive HTML charts | plotly 7.1.0 is 38 MB plus narwhals 2.3 MB, pure Python, released 2026-09-15 |
  | `excel` | `openpyxl>=3.1` | reading and writing workbooks | already what performance-report projects install |
  | `empyrical` | `empyrical-reloaded>=0.7,<0.8` | computing the metrics empyrical has through empyrical (2.7) | 138 MB and nine packages, with pandas and scipy |
  | `analytics` (unchanged in effect) | `toolbox` and `empyrical` | the analytics tools | as today, plus pygal |
  | none | pandas, if the firm has it | converting a `Table` to and from a DataFrame in a notebook | not a dependency of anything |

  Every generated application depends on `quant-agent-os[toolbox]`, so static charts work by default.
  `interactive` and `excel` are opt-in; performance-report adds `excel` because it reads administrators'
  workbooks. The supply-chain cool-off in `pyproject.toml` gains the three packages.

### 2.2 How the toolbox is organized

Seven subpackages in layers. A layer imports only the layers above it in this table, and
`tests/toolbox/test_layers.py` checks that the way `test_import_boundaries.py` checks the products.

| Layer | Subpackage | Holds | Comes from |
| --- | --- | --- | --- |
| 1 | `quant_toolbox.data` | `Table`, `Panel`, `TableError`; `load_table`, `load_csv`, `save_table` with its `.meta.json`, `read_meta`, `parse_date`, the `Loader` protocol; `load_settings` (TOML into a dataclass, unknown keys refused, types checked); seeded synthetic prices, returns, and positions. Optional leaves: `data.excel` (openpyxl), `data.frames` (`to_pandas`, `from_pandas`) | `tables.py`, `data.py`, `parameters.py`, `settings.py`, `synthetic.py`, `excel.py` |
| 2 | `quant_toolbox.performance` | returns from prices (`simple_returns`, `bar_returns` with delisting); annualised return and volatility, Sharpe, Sortino, Calmar, maximum drawdown, hit rate, turnover, beta, active metrics, `summarize`; horizons (MTD to ITD), monthly and calendar-year returns, wealth, underwater, drawdown episodes | `metrics.py`, half of `risk_metrics.py`, `horizons.py` |
| 2 | `quant_toolbox.risk` | value at risk, expected shortfall; exposures, concentration, and group exposures from weights; simulated history of today's book; Euler volatility contributions; factor exposures and variance shares; linear stress | `risk_metrics.py`, the generic half of risk-monitor's `risk.py`, `quant_analytics.risk` |
| 3 | `quant_toolbox.research` | ranks, information coefficients, Newey-West t-statistics, forward returns with the trade lag, quantile spreads, IC decay, autocorrelation; standard momentum and reversal; weights from a signal (dollar-neutral, beta-neutral, long-only); `run_backtest`, `BacktestResult`, walk-forward splits | `evaluation.py`, `signals.py`, `backtest.py`, `walk_forward.py` |
| 4 | `quant_toolbox.report` | `Document`, `TableBlock`, `ChartBlock`, `TextBlock`, `Column`; `render_markdown`, `render_html`, `to_dict`; chart builders (`equity_curve`, `underwater`, `drawdowns`, `rolling`, `bars`, `contributions`, `ic_decay`), each returning a `ChartBlock`. Leaves: `report.svg` (pygal, the default), `report.interactive` (plotly) | `document.py`, `report.py` |
| 4 | `quant_toolbox.audit` | `Provenance` (input fingerprints, parameters, code version, time), `fingerprint`, `code_version`, `save_run`, `latest_run`, `new_run_folder` | `results.py`, helpers in `monitor.py` and `reporting.py` |
| 5 | `quant_toolbox.testing` | bias checks a project's tests call in one line: `assert_no_look_ahead`, `assert_shift_changes_result`, `assert_costs_order_returns`, `assert_random_walk_shows_no_edge` | the tests copied into each project today |

Inside a subpackage, files stay small and private (`performance/_returns.py`, `performance/_horizons.py`),
and the subpackage's `__init__.py` re-exports its public names in `__all__`. A project imports from the
subpackage, never from a private file:

```python
from quant_toolbox.data import Table, load_table, save_table
from quant_toolbox.performance import sharpe_ratio, max_drawdown, horizon_return
from quant_toolbox.report import Document, TableBlock, equity_curve
```

Rules that keep imports optional and cheap:

- **numpy is the only module-level import** outside the standard library, in every file except the three
  optional leaves. `import quant_toolbox.data` works with numpy alone; a test imports every non-leaf module in
  an environment with numpy only.
- **An optional library is imported by the function that uses it,** through one helper,
  `quant_toolbox._optional.require("plotly", extra="interactive")`. A missing library raises
  `MissingExtraError` (an `ImportError`) naming the install command:
  `uv add "quant-agent-os[interactive]"`. Importing a leaf never fails; calling it does, with that message.
- **The top-level package imports nothing.** `quant_toolbox/__init__.py` holds `__version__`, `__all__`
  listing the seven subpackages, and a `features()` function that reports which optional parts are installed
  (static charts, interactive charts, Excel, pandas), for a project's `doctor` command and for bug reports.
  Subpackages load on first attribute access (PEP 562 `__getattr__`), with a `TYPE_CHECKING` block so strict
  mypy and editors still see them.
- **A chart is data until it is written.** Report sections build `ChartBlock`s; only `render_html` and
  `render_markdown` call a renderer, chosen by an argument (`charts="static"` by default, or
  `"interactive"`). So a section never imports pygal or plotly, and an application without the
  `interactive` extra runs every section.
- **Measured, not assumed.** Step 1.6 records the import time of `quant_toolbox` and of each subpackage, and
  the test suite fails if importing `quant_toolbox.performance` loads pygal, plotly, openpyxl, or pandas.

Every numeric function keeps the conventions the packs state today, and the known-answer tests move with
them. Parameters that a pack took from its `config` module (`TRADE_LAG`, `DELISTING_RETURN`, costs) become
explicit arguments with the same defaults, so the toolbox imports no application code.

### 2.3 Import rules between packages

| Package | May import | Never imports |
| --- | --- | --- |
| `quant_toolbox` | the standard library, numpy; pygal, plotly, openpyxl, and pandas only through `require` | Core, any product, `quant_platform`, pytemplate |
| `quant_agent_os` (Core) | as today | `quant_toolbox` |
| `quant_analytics` | `quant_toolbox` | other products |
| `quant_research`, `quant_app_factory` | as today | `quant_toolbox` (the factory's packs are files, not imports) |
| `quant_platform` | everything, as today | |

`tests/contract/test_import_boundaries.py` gains the row.
### 2.4 What stays in an application

From a quant lead's seat, an application should hold the decisions the firm makes and nothing else:

| Stays | Because |
| --- | --- |
| `config.toml` and its `settings`/`parameters` dataclass | the firm's data files, limits, horizons, costs, tear sheets |
| `firm/` (signals, rules, checks, sections, loaders) | the firm's alpha, rules, and formats |
| `cli.py` | the commands and their options |
| the orchestration (`study.py`, `backtest.run`, `monitor.py`, `reporting.py`) | which toolbox calls run, in what order, on which files |
| `sections.py` | the built-in tear-sheet sections, each a few lines over `performance` and `report` |
| the tests of the above | known-answer tests for the firm's own calculations; bias checks through `testing` |

Expected size, from today's line counts (estimates, to be measured in phase 4):

| Application | Today | After | Moves to the toolbox |
| --- | --- | --- | --- |
| alpha-research | 1,609 | about 650 | tables, metrics, evaluation, results, report, synthetic, the panel type |
| strategy-backtest | 2,067 | about 800 | the above, plus the backtest engine, weights, walk-forward splits |
| risk-monitor | about 1,830 | about 900 | tables, metrics, risk measures, the document model, synthetic, the generic risk functions |
| performance-report | 2,075 | about 950 | tables, metrics, risk measures, the document model, horizons, Excel, synthetic |

A generated project's test folder shrinks the same way: it keeps tests of its own code and the bias checks.

### 2.5 Stability

The toolbox is the thing a generated project pins, so its surface changes deliberately or not at all.

- **An API lock.** `tests/toolbox/api.lock` records every public name and signature per module, written and
  checked the way `check_app`'s schema lock is. A change fails the test until the lock is rewritten, and a
  rewrite needs a `CHANGELOG.md` entry under a "Quant Toolbox" heading. A removal or a changed signature is a
  minor bump of the wheel while it is 0.x, and a major one after 1.0. Decision 0015's deprecation window stays
  suspended, as the owner set, so nothing is kept for compatibility's sake.
- **Conventions are pinned by tests,** as decision 0010 does for analytics: each metric's known-answer test
  states the convention it checks, and `tests/toolbox/test_backends.py` holds the numpy backend to the
  empyrical one (it replaces `test_metric_parity`, which loaded a template file as a module).
- **A project pins a tag.** A generated `pyproject.toml` depends on `quant-agent-os[toolbox]` with a
  `[tool.uv.sources]` entry at the git tag of the factory that generated it, or at a local path for
  development. The question `quant_agent_os_source`, today asked for `agent-tools` only, is asked for every
  profile, and a new `quant_agent_os_ref` defaults to the generating wheel's version, which the factory passes
  as data. Upgrading is editing that tag and running the project's tests.
- **`__all__` in every module,** and nothing outside it is promised. Private helpers keep their underscore.

### 2.6 Charts

Tested on 2026-11-17 with 1,000 synthetic daily returns in a scratch environment:

| | pygal 3.1.3 | plotly 7.1.0 |
| --- | --- | --- |
| Size | 532 KB, two small deps | 38 MB plus narwhals; plotly express also needs pandas (another 54 MB) |
| Output | SVG, 38 KB for an equity curve; with `js=[]` no external script or URL beyond the SVG namespace | a 34 KB `<div>` that needs plotly.js: 4.8 MB inline, or a CDN link |
| Works offline | yes | with the 4.8 MB inline bundle, yes; the CDN form needs the viewer online |
| Python 3.11 to 3.15 | pure Python wheel | pure Python wheel |
| Chart types a report needs | line, date line, bar, stacked bar, box; no heatmap (the monthly table stays a table) | all |

Decisions (the owner's direction, 2026-11-17: static charts by default; plotly and pandas optional):

- **Static SVG is the default.** pygal renders it, replacing the two hand-written SVG routines, with `js=[]`
  so a report file is self-contained and carries no script. It is in the `toolbox` extra because every report
  has charts. A generated application's reports are static unless its `config.toml` asks for interactive ones.
- **plotly is optional,** behind the `interactive` extra, through its graph-objects API and never plotly
  express: express requires pandas. With `charts = "interactive"` in `config.toml`, `render_html` embeds
  plotly.js inline by default, so a report opens offline; a `cdn` option exists for a firm that prefers small
  files. plotly is not a dependency of any application unless the firm adds the extra.
- **pandas is optional everywhere.** The toolbox computes with numpy. `Table.to_pandas` and `from_pandas`
  import pandas inside the function, for notebooks, and nothing else in the toolbox touches it.
- **One `ChartBlock`, two renderers.** A section builds a block from data; the writer chooses SVG or HTML.
  Nothing in a section names a plotting library, so either can be replaced later.
- **Styles are the toolbox's,** one palette and one size, set in `report` and overridable by a `Style`
  argument, so every application's reports look the same.

### 2.7 One implementation for three users

Quant Toolbox is the only place a formula, a convention, or a data check is written. Each user adds only what
is its own:

| Shared in the toolbox | Quant Analytics adds | A generated application adds |
| --- | --- | --- |
| `performance`: every return metric, with its convention as data (`performance.CONVENTIONS`) | Pydantic inputs and outputs; `None` where the toolbox says NaN; `computed` evidence | the firm's settings and tear-sheet sections |
| `risk`: value at risk, exposures, Euler contributions, factor exposures, stress (`risk.CONVENTIONS`) | the same, for `analyze_risk` | limits, checks in `firm/checks.py` |
| `data.align`: series joined on shared dates, with a note for each series' left-out rows | dataset references resolved through Core's `DatasetStore` | file loaders in `firm/loaders.py` |
| `data.checks`: short sample, percent-like returns, gaps, outliers, as warning strings | warnings in the tool output | the report's data notes |
| `report.format_metric` and the metric kinds (percent, ratio, count) | evidence statements | report tables |

- **Quant Analytics computes through the toolbox.** `quant_analytics.metrics` and `quant_analytics.risk` are
  deleted; `computed.py` calls `quant_toolbox.performance` and `quant_toolbox.risk`, turns NaN into `None` at
  its boundary, and takes its conventions text from the toolbox. What stays in `quant_analytics` is the product:
  tool schemas, dataset resolution, warnings as tool output, and evidence. It holds no numerical formula.

#### empyrical first, numpy when it is not installed

The owner's direction, 2026-11-17: keep empyrical as a dependency, prefer it, and fall back to the toolbox's
numpy formulas when it is not installed. The empyrical adapter lives inside the toolbox, not beside it.

- **`quant_toolbox.performance` has two backends behind one API.** `performance/_empyrical.py` adapts
  empyrical-reloaded (today's `quant_analytics.metrics`, moved), and `performance/_numpy.py` holds the packs'
  formulas. Each public function, such as `sharpe_ratio`, calls empyrical when it is importable and numpy
  otherwise. Metrics empyrical lacks (information coefficients, Newey-West t-statistics, turnover, horizons,
  expected shortfall) and all of `risk` are numpy only. empyrical is never vendored.
- **The adapter cleans inputs, so both backends give the same number.** Measured on 2026-11-17, empyrical and
  the packs' numpy formulas agree to within 1e-15 on clean data, but not at the edges:

  | Input | Metric | numpy | empyrical, called directly |
  | --- | --- | --- | --- |
  | a constant 1% return | Sharpe | NaN | 4.5e16 |
  | a constant 1% return | volatility | 0 | 5.6e-17 |
  | no negative returns, or two points | Sortino | NaN | inf |
  | 504 returns with 11 missing | annualised return | -21.15% | -20.74% |
  | 504 returns with 11 missing | value at risk | -1.64% | NaN |

  So before calling empyrical the adapter drops missing values, as the analytics tools already do; it
  answers NaN for a series that does not vary, as `quant_analytics.metrics` already does for Sharpe; and it
  turns inf into NaN. A parametrized test runs every shared metric through both backends on clean data and on
  each case above, and requires the same answer to within 1e-12. That test, not the order of preference, is
  what makes the fallback safe.
- **Which backend ran is always stated.** `performance.backend()` returns `"empyrical-reloaded 0.7.x"` or
  `"numpy"`. Quant Analytics puts it in each output's `conventions["source"]`, as today; a generated report's
  data notes and `audit.Provenance` record it. `QUANT_TOOLBOX_BACKEND=numpy` (or `backend="numpy"` in a call)
  forces numpy, for a firm that wants one implementation on every machine.
- **Who installs what.** The `analytics` extra keeps empyrical, so the platform computes as decision 0010
  says. A generated application depends on `quant-agent-os[toolbox]` and runs on numpy, since empyrical
  brings pandas and scipy and the owner wants pandas optional. A firm that wants empyrical in its
  applications adds the `empyrical` extra, and its numbers do not change, by the test above.
- **CI runs both.** The toolbox tests run once with empyrical installed and once without, so the fallback is
  never the untested path.
- **Python 3.15** keeps decision 0010's caveat for empyrical (a `bottleneck` source build). The numpy backend
  has none, which is one more reason applications default to it.

#### Why inside the toolbox, not independent

Keeping the empyrical adapter in `quant_analytics` would leave two public APIs for the same metrics, each
with its own input cleaning, NaN rules, and conventions text: the repetition this plan removes. Inside the
toolbox, both backends share one signature, one cleaning step, one conventions table, and one test that holds
them equal, and Quant Analytics, the packs, and applications call the same function.
- **Core keeps `infer_periods_per_year`.** It is stdlib-only, and Core lists datasets with no numpy installed,
  so Core cannot import the toolbox. The toolbox has its own copy in `data`, and
  `tests/toolbox/test_parity.py` holds the two to the same answers on the same dates. This is the one
  deliberate duplicate.
- **`ColumnRef` stays in `quant_analytics.references`:** it names data by reference, a platform idea, and the
  toolbox has no datasets.
- **The tool surface does not change:** same names, schemas, and numbers. The analytics known-answer tests
  run unchanged against the new code, which is the check that nothing moved. The agent-efficiency evaluation
  runs and reports its delta, expected zero.

### 2.8 The packs after the change

- **`quant-apps` 2.0.0 and `quant-baseline` 4.0.0,** major bumps: a project generated from them depends on
  the toolbox. The `metrics` component of `quant-baseline` goes, because the dependency is unconditional.
- **A project's `AGENTS.md`** says where formulas come from and how to take a newer toolbox, in place of
  "do not change a formula in `metrics.py`": "formulas come from Quant Toolbox at the version
  `pyproject.toml` pins; to use another definition, add a separately named figure in this project."
- **The planner's `portable` note** is rewritten: a project depends on the platform's wheel for its MIT
  library only, and on the platform's server only with `agent-tools`. Whether the field stays is settled in
  phase 4.
- **`test_quant_apps.py`** drops the identity checks for files that no longer exist and loads nothing from a
  template file as a module; each application's own tests run as today, offline, against the toolbox in this
  checkout.
- **The tutorial** generates a risk-monitor and customizes it at four levels; each lab is re-run and its
  quoted output refreshed.

## 3. Steps

Each phase is one PR, one commit per step, and the owner merges. The package is usable from phase 1 on; the
packs switch in phase 4, so until then nothing a user generates changes.

### Phase 1: the package and its numbers

- [ ] 1.1 `src/quant_toolbox/` with the lazy top-level `__init__`, `_optional.require`,
      `MissingExtraError`, and `features()`; then `data`, `performance`, `risk`, and `audit`, moved from the
      packs with their docstrings and conventions, each re-exporting its public names in `__all__`.
- [ ] 1.2 `tests/toolbox/`: the packs' known-answer tests moved and deduplicated; `test_parity.py` for
      Core's frequency inference against the toolbox's; `tests/factory/test_metric_parity.py` deleted; the API
      lock and its test.
- [ ] 1.3 The `toolbox` extra (numpy, pygal), the cool-off dates, the sixth `module-name`, `known-first-party`,
      and the MIT `LICENSE` in `license-files`.
- [ ] 1.4 `test_import_boundaries.py`: the toolbox row. `tests/toolbox/test_layers.py`: layers import
      only upward, no private file is imported across subpackages, and no module outside the optional
      leaves imports a package beyond numpy at module level. A test in a numpy-only environment imports
      every non-leaf module, and one calls each leaf without its extra and expects `MissingExtraError`.
- [ ] 1.5 The two backends: `performance/_empyrical.py` (moved from `quant_analytics.metrics`, with input
      cleaning) and `performance/_numpy.py`, which gains omega, tail ratio, stability, and alpha;
      `performance.backend()`, `QUANT_TOOLBOX_BACKEND`; `tests/toolbox/test_backends.py` holding both equal on
      clean data and on the edge cases in 2.7. The `empyrical` extra; `analytics` becomes `toolbox` plus
      `empyrical`. CI runs the toolbox tests with and without empyrical.
- [ ] 1.5a Quant Analytics on the toolbox: `quant_analytics.metrics` and `.risk` deleted; `computed.py` and
      `tools.py` call `performance`, `risk`, `data.align`, `data.checks`, and `report.format_metric`, and name
      the backend in `conventions["source"]`. The analytics tests pass unchanged.
- [ ] 1.6 Measure a fresh install of `quant-agent-os[toolbox]` from a local path on Python 3.11 and 3.15:
      packages, size, and the import time of `quant_toolbox` and of each subpackage (`-X importtime`).
- [ ] 1.7 Docs: `docs/products/quant-toolbox.md` (what it holds, conventions, how to pin it, how to upgrade),
      the architecture page's package table and import rules, `AGENTS.md`'s product list and invariants.
- [ ] 1.8 Decision 0026, "One toolbox under Quant Analytics and the generated applications": amends 0009
      (portability), 0010 (empyrical is the toolbox's preferred backend, numpy the fallback), 0018
      (six import names), 0019 (MIT list), 0022 (`metrics.py` is no longer copied). The changelog.

### Phase 2: reports and charts

- [ ] 2.1 `report`: the document model, the chart builders as data, and the Markdown, HTML, and JSON
      renderers, with `report.py`'s results-dict rendering rewritten over it.
- [ ] 2.2 `report.svg` on pygal with `js=[]`, the default renderer: a `Style`, and a test that an SVG holds
      no `<script>` and no URL beyond the SVG namespace.
- [ ] 2.3 `report.interactive` behind the `interactive` extra: plotly graph objects, inline plotly.js by
      default. A test rendering every chart type both ways, skipped with a reason when plotly is absent.
- [ ] 2.4 `data.excel` behind the `excel` extra, moved from performance-report with its tests; `data.frames`
      with pandas imported on call.
- [ ] 2.5 Docs and changelog.

### Phase 3: signals, portfolios, backtests, and bias checks

- [ ] 3.1 `research`: evaluation statistics and the two standard definitions, with the trade lag and
      delisting return as arguments.
- [ ] 3.2 `research`'s weights and backtest engine, from `strategy-backtest` (the newer of the two copies; the
      75 lines that differ from `quant-lab`'s are reviewed one by one and the test that explains each kept).
- [ ] 3.3 `testing`: the bias checks, written so a project's test file is one call each.
- [ ] 3.4 Docs and changelog.

### Phase 4: the packs

- [ ] 4.1 `alpha-research` on the toolbox: delete the moved files, rewrite the imports, trim the tests to the
      project's own code plus the `testing` calls; `pyproject.toml.jinja` pins the tag; `AGENTS.md` and
      `LIMITATIONS.md` reworded.
- [ ] 4.2 `strategy-backtest`, 4.3 `risk-monitor`, 4.4 `performance-report`, the same way.
- [ ] 4.5 `quant-baseline` (`quant-lab`) the same way, without the `metrics` component.
- [ ] 4.6 The planner: `quant_agent_os_source` for every profile, `quant_agent_os_ref` with the wheel's
      version as default, the `portable` wording; `test_quant_apps.py` and the factory smoke test.
- [ ] 4.7 Measure each application's line count before and after and put the table in the product page.
- [ ] 4.8 The tutorial re-run on a generated risk-monitor, outputs refreshed; the product page's "Generated
      metrics and the platform's" section rewritten as "Formulas come from Quant Toolbox"; the changelog with
      the two pack versions.
- [ ] 4.9 The agent-efficiency evaluation, reported; rebaseline only if a tool's output changed, which none
      should.

### Phase 5: release

- [ ] 5.1 Verify: tests, lint, format, types, the opt-in factory smoke test, the research evaluation, and a
      clean install of `quant-agent-os[toolbox]` from the tag on every CI Python.
- [ ] 5.2 Release 0.5.0, on the owner's say, with the toolbox named in the notes as 0.x: its API may still
      change between minors until 1.0.

## 4. Acceptance criteria

- No `.py` file under `src/quant_app_factory/packs/` defines a metric, a risk measure, a table reader, a
  document renderer, a chart, or a run folder; `grep` for `def annualized_return`, `class Table`,
  `def render_markdown`, `def save_run` finds them in `src/quant_toolbox/` only.
- Each generated application's package is at most 60% of its size today, measured by the step 4.7 table.
- A generated project's tests pass offline against the pinned toolbox, with `uv sync` adding numpy, pygal,
  and the base wheel's four packages and nothing else, unless an extra was chosen.
- With numpy alone installed, every subpackage and every optional leaf imports. Rendering a report writes
  static SVG charts with the `toolbox` extra alone; asking for interactive charts without plotly raises
  `MissingExtraError` naming `quant-agent-os[interactive]`, and Excel without openpyxl names
  `quant-agent-os[excel]`.
- `import quant_toolbox` loads no subpackage, and importing `quant_toolbox.performance` loads none of pygal,
  plotly, openpyxl, or pandas.
- A generated project imports only from the seven public subpackages, never from a private file.
- A report's SVG contains no `<script>` and no URL beyond the SVG namespace.
- `quant_analytics` defines no numerical formula and imports no numpy or empyrical; its tools return the same
  numbers as before, checked by its existing known-answer tests, and name the backend that computed them.
- `tests/toolbox/test_backends.py` holds the two backends to within 1e-12 on every shared metric, on clean
  data and on constant, all-positive, two-point, and gappy series; CI runs the toolbox tests with and without
  empyrical.
- Each item in section 1's two tables exists once, except Core's `infer_periods_per_year`, which a parity test
  holds to the toolbox's.
- A change to any public toolbox signature fails `tests/toolbox/test_api_lock.py` until the lock and the
  changelog are updated.
- `test_import_boundaries.py` fails if Core, Quant Research, or the factory imports `quant_toolbox`, or if
  the toolbox imports anything of the platform.
- The tool surface, the skills, and the agent-efficiency baseline are unchanged.

## 5. Open questions for the owner

Each needs an answer before the phase that depends on it.

- **5.1 The MIT wording (phase 1).** The toolbox is MIT code inside a wheel that is mostly BUSL. Decision 0019
  already does this for the packs; counsel should confirm it reads the same for an importable module.
- **5.2 The name (phase 1).** `quant_toolbox`, "Quant Toolbox", and the `toolbox` extra follow the other
  packages. `quant_tool_box` and `helper` were suggested; the first reads oddly beside `quant_analytics`, and
  the second says nothing about what it holds.
- **5.3 Applications' default backend (phase 4).** The plan gives generated applications numpy by default,
  because empyrical brings pandas and scipy. If the owner prefers empyrical there too, the packs add the
  `empyrical` extra and nothing else changes.
- **5.4 `quant-lab` (phase 4).** Decision 0022 keeps it as the all-in-one sample. Once the four applications
  are each a few hundred lines over the toolbox, the sample may have no reader; dropping it would also drop a
  public mirror and a hub entry.
- **5.5 The public mirrors (phase 5).** `quant-apps` 2.0.0 and `quant-baseline` 4.0.0 would need new mirror
  builds and hub entries, as before, by hand.

## 6. Outside this repository

None of these block the steps above:

- Publishing the new pack versions to their public mirrors and listing them in the index of packs.
- Counsel's review of decision 0019 with the toolbox added.
- Nothing in pytemplate-uv changes: the packs' questions and data are its public `pytemplate.api` as today.
