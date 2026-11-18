"""The conventions of every metric, as text a report or a tool's output can carry."""

from __future__ import annotations

from quant_toolbox.performance._backend import source

_CONVENTIONS = {
    "returns": "simple per-period returns, as fractions",
    "annualized_return": "geometric",
    "annualized_volatility": "sample standard deviation, times sqrt(periods per year)",
    "sharpe_ratio": "mean excess over sample deviation, times sqrt(periods per year); "
    "the annual risk-free rate is divided by periods per year",
    "sortino_ratio": "mean return over downside deviation (below zero), annualised",
    "calmar_ratio": "annualised return over the size of the maximum drawdown",
    "omega_ratio": "probability-weighted gains over losses around a zero return",
    "max_drawdown": "peak-to-trough on compounded wealth, as a negative fraction",
    "tail_ratio": "95th percentile return over the absolute 5th percentile",
    "stability": "R squared of a linear fit to cumulative log returns",
    "value_at_risk": "5th percentile of per-period returns (historical)",
    "hit_rate": "share of periods with a positive return",
    "alpha": "per-period intercept against the benchmark, compounded over a year",
    "beta": "OLS slope on the benchmark",
    "active_return": "annualised arithmetic mean of returns minus benchmark",
    "tracking_error": "annualised volatility of active returns",
    "information_ratio": "excess Sharpe against the benchmark, annualised",
}


def conventions(backend: str | None = None) -> dict[str, str]:
    """Every metric's convention, with ``source`` naming the backend that computes them."""
    return {"source": source(backend), **_CONVENTIONS}
