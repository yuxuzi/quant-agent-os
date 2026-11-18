"""The conventions of the risk decomposition, as text a tool's output can carry."""

from __future__ import annotations

from quant_toolbox.risk._decomposition import FLAT
from quant_toolbox.risk._liquidity import LIQUIDITY_BUCKETS
from quant_toolbox.risk._portfolio import EWMA_DECAY

CONVENTIONS = {
    "source": "numpy sample covariance and least squares (empyrical has no risk decomposition)",
    "returns": "simple per-period returns, as fractions, aligned by period",
    "weights": "constant over the sample (rebalanced every period); may be negative",
    "volatility": "sqrt(w' S w), S the sample covariance (n - 1) times periods per year",
    "contributions": "Euler: marginal (S w)_i / vol, component w_i * marginal, percent "
    "component / vol; components sum to volatility, percents to one; none below "
    f"{FLAT:g} of sum(|w_i| vol_i)",
    "diversification_ratio": "sum(|w_i| vol_i) / portfolio volatility",
    "exposures": "OLS slopes on all factors at once, with an intercept; the portfolio's is "
    "the weighted sum of asset exposures",
    "variance_shares": "b_k (F b)_k / var in sample, F the factor covariance; the specific "
    "share is residual variance / var; shares sum to one and factor shares to R squared",
}
FACTOR_CONVENTIONS = frozenset({"exposures", "variance_shares"})

# The measures added in quant-toolbox 0.6.0, kept apart from CONVENTIONS, which Quant Analytics
# passes through whole to its tools' output.
MEASURE_CONVENTIONS = {
    "herfindahl": "sum of squared shares of gross exposure; effective positions is 1 / it",
    "liquidity": "days = |position| / (participation * ADV), positions sold in parallel; the "
    "share within d days is sum(min(|p|, d * participation * ADV)) / gross; buckets "
    f"{', '.join(str(b) for b in LIQUIDITY_BUCKETS)} days (Form PF style)",
    "historical_stress": "each asset compounded over the window's bars after its start, a "
    "missing return as 0, applied linearly to today's weights; None when not covered",
    "factor_risk": "covariance B F B' + D from the supplied model; Euler factor shares "
    "x_k (F x)_k / var and specific share sum to one; asset contributions to volatility",
    "style_regression": "OLS with an intercept on supplied factor returns; alpha is the "
    "per-period intercept times periods per year; Newey-West (Bartlett) t-statistics",
    "correlation": "Pearson, over the bars where every series is finite",
    "expected_shortfall_contributions": "mean of w_i r_i over the bars where the book is at or "
    "below its value at risk; they sum to the book's expected shortfall",
    "ewma_covariance": f"RiskMetrics: weights decay ** k (default {EWMA_DECAY}), normalised, "
    "zero mean, times periods per year",
}
