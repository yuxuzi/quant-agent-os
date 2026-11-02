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
  Python 3.15 it builds from source. It did on every CI runner (Linux, macOS, and Windows) when
  this was decided. A machine without a working C toolchain cannot install the package on 3.15
  until bottleneck ships cp315 wheels.
- `peewee<3.17.4` arrives through empyrical, an unusual pin for a metrics library. It is locked
  under the same cut-off as everything else.
- **Risk (B-21, 2026-11-01).** empyrical has no portfolio risk decomposition, so
  `quant_analytics.risk` uses numpy's sample covariance and least squares. These are not new
  formulas to defend in the sense above. Euler contributions summing to volatility, and factor
  plus specific variance shares summing to one, are identities that the known-answer tests
  check. The single-factor beta is also checked against empyrical's. The module follows the
  same rules as `metrics.py`: it imports numerical libraries only, and its conventions are
  stated and pinned by tests.
