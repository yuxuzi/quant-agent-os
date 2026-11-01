# 0010: Quant Analytics computes metrics with empyrical-reloaded, in the workspace

- Status: accepted
- Date: 2026-11-01

## Context

Backlog B-13 asks for an analytics package with performance metrics, documented conventions,
and known-answer tests, registered as tools. Writing the formulas ourselves means maintaining
and defending them. The quant ecosystem already has a trusted implementation: empyrical, the
metrics library behind pyfolio (both maintained as `-reloaded` forks).

We measured both on Python 3.14, under the repository's two-week supply-chain cut-off:

| | pyfolio-reloaded 0.10.0 | empyrical-reloaded 0.7.0 |
| --- | --- | --- |
| Install | 322 MB, 41 packages (matplotlib, seaborn, scikit-learn, IPython, scipy, pandas, ...) | 138 MB, 9 packages (numpy, pandas, scipy, bottleneck, peewee, pytz, ...) |
| Metric numbers | delegates to empyrical | the source |
| Annualisation | `daily`, `weekly`, or `monthly` only (`perf_stats` assumes daily) | any periods per year |
| Adds | matplotlib tear sheets for notebooks | Sortino, Calmar, Omega, tail ratio, VaR, stability, alpha and beta |

## Decision

- **empyrical-reloaded computes the metrics.** `quant_analytics.metrics` only adapts:
  - lists go in and plain floats come out;
  - an undefined metric is `None`, never `inf` or `NaN`.
  - Hit rate, active return, tracking error, and information ratio are composed from
    empyrical's own functions (annualised volatility and excess Sharpe of active returns).
- **pyfolio is not used.** It adds plotting, not metrics, at more than twice the footprint, and
  our tools return JSON for agents. Charts will be our own, later, like the SVG equity curves
  of the quant-research projects.
- **Conventions are pinned by tests.** Known-answer tests check each formula against the
  stated convention, so an empyrical upgrade cannot change a number silently:
  - the sample standard deviation;
  - a geometric annual return;
  - the risk-free rate per period;
  - a compounded alpha;
  - Sortino's downside deviation.
- **The package lives in the workspace.** `packages/quant-analytics` (`quant-agent-os-analytics`)
  revises decision 0003, which planned a separate repository. It moves out when it has its own
  release cycle.
  - `metrics.py` imports numerical libraries only.
  - `tools.py` imports Core's public API, like a product.
  - No product imports it.
- **The dependency stays here.** pandas and scipy enter this package only, never Core or the
  products. The generated quant-research projects keep their own numpy metrics (decision 0009):
  they are portable code the user owns, and their core metrics (IC, Newey-West t-statistics)
  are not in empyrical.

## Consequences

- No hand-written formulas to defend. The metric names and values are the ones pyfolio users
  already know.
- **Python 3.15.** bottleneck, which empyrical requires, publishes no cp315 wheels, so on
  Python 3.15 it builds from source. CI runners have compilers, but this is the riskiest part of
  the dependency. If it fails, the options are a source-build note, a bottleneck release with
  cp315 wheels, or dropping 3.15 for this one package.
- `peewee<3.17.4` arrives through empyrical, an unusual pin for a metrics library. It is locked
  under the same cut-off as everything else.
