"""Risk from a factor model a vendor supplies: exposures, factor covariance, specific variance.

Conventions:
- The asset covariance is ``B F B' + D``: ``B`` the assets' factor loadings (a missing loading is
  0), ``F`` the annualised factor covariance, and ``D`` the diagonal of annualised specific
  (idiosyncratic) variances. Weights are a share of NAV, shorts negative.
- The portfolio's factor exposures are ``x = B' w``; factor variance is ``x' F x``, specific
  variance ``sum(w_i^2 d_i)``, and total variance their sum.
- Factor k's share of total variance is ``x_k (F x)_k / var`` (Euler); with the specific share,
  ``specific / var``, the shares sum to one. A negative share means the factor hedges the rest.
- An asset's contribution to volatility is ``w_i (S w)_i / vol`` (Euler); they sum to the
  volatility. Shares and contributions are None when the volatility is zero.
- This module estimates nothing: it applies the model it is given.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

SYMMETRY = 1e-10  # relative asymmetry a factor covariance may have from rounding


@dataclass(frozen=True)
class FactorRisk:
    """A portfolio's volatility under a factor model, split into factors, specific, and assets."""

    volatility: float
    factor_volatility: float
    specific_volatility: float
    exposures: dict[str, float]
    factor_shares: dict[str, float] | None
    specific_share: float | None
    contributions: dict[str, float] | None


def factor_risk(
    weights: Mapping[str, float],
    exposures: Mapping[str, Mapping[str, float]],
    factor_covariance: Mapping[str, Mapping[str, float]],
    specific_variance: Mapping[str, float],
) -> FactorRisk:
    """Volatility of ``weights`` under a factor model, with Euler shares and contributions.

    ``exposures`` maps asset to factor to loading; ``factor_covariance`` maps factor to factor to
    an annualised covariance (one triangle is enough); ``specific_variance`` maps asset to an
    annualised variance and must name every asset held.
    """
    names, factors = list(weights), list(factor_covariance)
    unknown = sorted({f for a in names for f in exposures.get(a, {})} - set(factors))
    if unknown:
        raise ValueError(f"Loadings on factors with no covariance: {', '.join(unknown)}.")
    missing = [name for name in names if name not in specific_variance]
    if missing:
        raise ValueError(f"No specific variance for {', '.join(missing)}.")
    w = np.array([float(weights[name]) for name in names])
    loadings = np.array(
        [[float(exposures.get(name, {}).get(f, 0.0)) for f in factors] for name in names]
    ).reshape(len(names), len(factors))
    f_cov = np.array([[_covariance(factor_covariance, a, b) for b in factors] for a in factors])
    if not np.allclose(f_cov, f_cov.T, rtol=SYMMETRY, atol=0.0):
        raise ValueError("The factor covariance is not symmetric.")
    specific = np.array([float(specific_variance[name]) for name in names])
    portfolio = loadings.T @ w
    factor_variance = float(portfolio @ f_cov @ portfolio)
    specific_variance_total = float(np.sum(w**2 * specific))
    variance = factor_variance + specific_variance_total
    volatility = math.sqrt(max(variance, 0.0))
    named = {f: float(portfolio[k]) for k, f in enumerate(factors)}
    if volatility <= 0.0:
        return FactorRisk(
            volatility=0.0,
            factor_volatility=math.sqrt(max(factor_variance, 0.0)),
            specific_volatility=math.sqrt(max(specific_variance_total, 0.0)),
            exposures=named,
            factor_shares=None,
            specific_share=None,
            contributions=None,
        )
    covariance = loadings @ f_cov @ loadings.T + np.diag(specific)
    component = w * (covariance @ w) / volatility
    shares = portfolio * (f_cov @ portfolio) / variance
    return FactorRisk(
        volatility=volatility,
        factor_volatility=math.sqrt(max(factor_variance, 0.0)),
        specific_volatility=math.sqrt(max(specific_variance_total, 0.0)),
        exposures=named,
        factor_shares={f: float(shares[k]) for k, f in enumerate(factors)},
        specific_share=specific_variance_total / variance,
        contributions={name: float(component[i]) for i, name in enumerate(names)},
    )


def _covariance(table: Mapping[str, Mapping[str, float]], left: str, right: str) -> float:
    if right in table.get(left, {}):
        return float(table[left][right])
    if left in table.get(right, {}):
        return float(table[right][left])
    raise ValueError(f"No factor covariance for {left} and {right}.")
