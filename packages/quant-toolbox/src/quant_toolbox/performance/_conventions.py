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
    "up_capture": "mean return over mean benchmark return, on bars the benchmark rose",
    "down_capture": "mean return over mean benchmark return, on bars the benchmark fell",
    "up_beta": "OLS slope on the benchmark, on bars the benchmark rose",
    "down_beta": "OLS slope on the benchmark, on bars the benchmark fell",
    "correlation": "Pearson correlation with the benchmark",
    "rolling": "trailing window ending at each bar; NaN until full or if it holds a gap; "
    "annualised with periods per year, never the window",
    "skewness": "bias-adjusted sample skewness (G1)",
    "excess_kurtosis": "bias-adjusted sample excess kurtosis (G2); 0 for a normal distribution",
    "gain_loss_ratio": "mean positive return over the size of the mean negative return",
    "profit_factor": "sum of positive returns over the size of the sum of negative returns",
    "sharpe_standard_error": "Mertens (2002), with skew and kurtosis, of the annualised Sharpe",
    "probabilistic_sharpe_ratio": "probability the true Sharpe exceeds the benchmark Sharpe "
    "(Bailey and Lopez de Prado, 2012)",
    "deflated_sharpe_ratio": "probabilistic Sharpe against the expected best of the trials "
    "(Bailey and Lopez de Prado, 2014)",
    "minimum_track_record": "bars needed for the probabilistic Sharpe to reach the confidence",
    "parametric_var": "mean plus the normal quantile times the sample deviation, per bar; "
    "Cornish-Fisher adjusts the quantile for skew and excess kurtosis",
    "parametric_expected_shortfall": "Gaussian: mean minus deviation times phi(z) / cutoff",
    "brinson": "single-period Brinson-Fachler: allocation, selection, interaction; no linking",
}


def conventions(backend: str | None = None) -> dict[str, str]:
    """Every metric's convention, with ``source`` naming the backend that computes them."""
    return {"source": source(backend), **_CONVENTIONS}
