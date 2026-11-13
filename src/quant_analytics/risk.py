"""Portfolio risk: volatility contributions and factor exposures, with conventions stated.

empyrical has no risk decomposition, so this module uses numpy's sample covariance and least
squares (decision 0010). Both decompositions are exact identities, checked by known-answer tests:
- Returns are simple per-period returns, one series per asset (and per factor), aligned by
  period. Weights are constant over the sample (rebalanced every period), may be negative, and
  need not sum to one.
- The covariance is the sample covariance (``n - 1``) times ``periods_per_year``, so portfolio
  volatility ``sqrt(w' S w)`` equals the annualised volatility of the weighted return series.
- Contributions are Euler's: the marginal contribution of asset i is ``(S w)_i / vol``, its
  component contribution is ``w_i`` times that, and components sum to the portfolio volatility.
  Percent contributions sum to one; a negative one means the position hedges the rest.
- The diversification ratio is ``sum(|w_i| vol_i) / vol``; it is at least one for long-only
  portfolios.
- Factor exposures are OLS slopes of each asset's returns on all factors at once, with an
  intercept. The portfolio's exposure is the weighted sum of asset exposures, which equals the
  slope of the portfolio's own regression.
- Factor variance shares split the portfolio variance in sample: factor k's share is
  ``b_k (F b)_k / var``, where ``F`` is the factor covariance, and the specific share is the
  residual variance over var. Shares sum to one; the factor shares sum to R squared.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
# Portfolio volatility below this share of its positions' own (sum of |w_i| vol_i) is rounding
# left over from an exact hedge, so it counts as zero rather than as a tiny denominator.
FLAT = 1e-6


@dataclass(frozen=True)
class Contribution:
    """One asset's share of portfolio volatility (annualised)."""

    weight: float
    volatility: float
    marginal: float | None
    component: float | None
    percent: float | None


@dataclass(frozen=True)
class VolatilityBreakdown:
    """Portfolio volatility and how each asset contributes to it."""

    volatility: float
    contributions: dict[str, Contribution]
    diversification_ratio: float | None
    gross_exposure: float
    net_exposure: float


@dataclass(frozen=True)
class FactorBreakdown:
    """Factor exposures and the share of portfolio variance each factor explains."""

    exposures: dict[str, float]
    asset_exposures: dict[str, dict[str, float]]
    variance_shares: dict[str, float] | None
    specific_share: float | None
    r_squared: float | None
    collinear: bool


def volatility_contributions(
    weights: Mapping[str, float],
    asset_returns: Mapping[str, Sequence[float]],
    periods_per_year: int,
) -> VolatilityBreakdown:
    """Decompose portfolio volatility into Euler contributions; None where volatility is zero.

    Volatility that is only rounding left over from an exact hedge counts as zero (`FLAT`).
    """
    names = list(weights)
    w = np.array([weights[name] for name in names], dtype=np.float64)
    returns = _matrix([asset_returns[name] for name in names])
    covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1)) * periods_per_year
    volatilities = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    volatility = math.sqrt(max(float(w @ covariance @ w), 0.0))
    if volatility <= FLAT * float(np.abs(w) @ volatilities):
        volatility = 0.0
    defined = volatility > 0.0
    marginal = covariance @ w / volatility if defined else None
    contributions = {
        name: Contribution(
            weight=float(w[i]),
            volatility=float(volatilities[i]),
            marginal=None if marginal is None else float(marginal[i]),
            component=None if marginal is None else float(w[i] * marginal[i]),
            percent=None if marginal is None else float(w[i] * marginal[i] / volatility),
        )
        for i, name in enumerate(names)
    }
    return VolatilityBreakdown(
        volatility=volatility,
        contributions=contributions,
        diversification_ratio=float(np.abs(w) @ volatilities / volatility) if defined else None,
        gross_exposure=float(np.abs(w).sum()),
        net_exposure=float(w.sum()),
    )


def factor_exposures(
    weights: Mapping[str, float],
    asset_returns: Mapping[str, Sequence[float]],
    factor_returns: Mapping[str, Sequence[float]],
) -> FactorBreakdown:
    """Regress each asset on the factors; split the portfolio's variance among them.

    Needs more periods than factors plus one. Shares and R squared are None when the portfolio
    returns do not vary. ``collinear`` means the factors are linearly dependent, so the
    exposures are one of many solutions (the smallest) and should not be read one by one.
    """
    names, factors = list(weights), list(factor_returns)
    w = np.array([weights[name] for name in names], dtype=np.float64)
    returns = _matrix([asset_returns[name] for name in names])
    f = _matrix([factor_returns[factor] for factor in factors])
    design = np.column_stack([np.ones(len(f)), f])
    collinear = int(np.linalg.matrix_rank(design)) < design.shape[1]
    coefficients, *_ = np.linalg.lstsq(design, returns, rcond=None)
    betas = coefficients[1:]  # factors x assets
    exposures = betas @ w
    portfolio = returns @ w
    variance = float(np.var(portfolio, ddof=1))
    scale = float(np.abs(w) @ np.std(returns, axis=0, ddof=1))
    if math.sqrt(max(variance, 0.0)) <= FLAT * scale:
        variance = 0.0
    residual = portfolio - design @ (coefficients @ w)
    asset_exposures = {
        name: {factor: float(betas[k, i]) for k, factor in enumerate(factors)}
        for i, name in enumerate(names)
    }
    if variance <= 0.0:
        return FactorBreakdown(
            exposures=_named(factors, exposures),
            asset_exposures=asset_exposures,
            variance_shares=None,
            specific_share=None,
            r_squared=None,
            collinear=collinear,
        )
    factor_covariance = np.atleast_2d(np.cov(f, rowvar=False, ddof=1))
    shares = exposures * (factor_covariance @ exposures) / variance
    return FactorBreakdown(
        exposures=_named(factors, exposures),
        asset_exposures=asset_exposures,
        variance_shares=_named(factors, shares),
        specific_share=float(np.var(residual, ddof=1)) / variance,
        r_squared=float(shares.sum()),
        collinear=collinear,
    )


def _matrix(columns: list[Sequence[float]]) -> FloatArray:
    """Stack series as columns: periods x series."""
    return np.column_stack([np.asarray(column, dtype=np.float64) for column in columns])


def _named(names: list[str], values: FloatArray) -> dict[str, float]:
    return {name: float(values[i]) for i, name in enumerate(names)}
