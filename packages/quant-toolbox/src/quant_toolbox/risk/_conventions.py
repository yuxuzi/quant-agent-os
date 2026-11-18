"""The conventions of the risk decomposition, as text a tool's output can carry."""

from __future__ import annotations

from quant_toolbox.risk._decomposition import FLAT

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
