"""Quant-Agent-OS tools over `quant_analytics.metrics` and `.risk`: typed and deterministic.

They need no permissions: every input arrives in the call.

Every output carries the metric conventions, warnings for short samples, and ``computed``
evidence that names the series it came from.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import ToolRegistry, ToolSpec, tool
from quant_analytics import metrics, risk

MIN_OBSERVATIONS = 12
MAX_ASSETS = 500
MAX_FACTORS = 50
CONVENTIONS = {
    "source": "empyrical-reloaded (the metrics behind pyfolio)",
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


RISK_CONVENTIONS = {
    "source": "numpy sample covariance and least squares (empyrical has no risk decomposition)",
    "returns": "simple per-period returns, as fractions, aligned by period",
    "weights": "constant over the sample (rebalanced every period); may be negative",
    "volatility": "sqrt(w' S w), S the sample covariance (n - 1) times periods per year",
    "contributions": "Euler: marginal (S w)_i / vol, component w_i * marginal, percent "
    "component / vol; components sum to volatility, percents to one",
    "diversification_ratio": "sum(|w_i| vol_i) / portfolio volatility",
    "exposures": "OLS slopes on all factors at once, with an intercept; the portfolio's is "
    "the weighted sum of asset exposures",
    "variance_shares": "b_k (F b)_k / var in sample, F the factor covariance; the specific "
    "share is residual variance / var; shares sum to one and factor shares to R squared",
}
FACTOR_CONVENTIONS = frozenset({"exposures", "variance_shares"})


class ReturnsInput(BaseModel):
    """Periodic simple returns for one series."""

    series_id: str = Field(min_length=1, description="Identifier of the returns series")
    returns: list[float] = Field(min_length=2, description="Simple returns, e.g. 0.01 for 1%")
    periods_per_year: int = Field(default=12, ge=1, le=366)

    @field_validator("returns")
    @classmethod
    def _finite(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) and value > -1.0 for value in values):
            raise ValueError("returns must be finite and above -1 (a loss of more than 100%)")
        return values


class PerformanceInput(ReturnsInput):
    """Returns, and optionally a benchmark over the same periods and an annual risk-free rate."""

    benchmark: list[float] | None = Field(
        default=None, description="Benchmark returns, one per period of returns"
    )
    risk_free: float = Field(default=0.0, ge=-0.1, le=0.5, description="Annual risk-free rate")

    @model_validator(mode="after")
    def _aligned(self) -> PerformanceInput:
        if self.benchmark is not None:
            if len(self.benchmark) != len(self.returns):
                raise ValueError("benchmark must have one value per period of returns")
            if not all(math.isfinite(value) for value in self.benchmark):
                raise ValueError("benchmark returns must be finite")
        return self


def _check_series(label: str, series: dict[str, list[float]], loss_floor: bool) -> int:
    """Return the common length of ``series`` or raise with the offending names."""
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1:
        raise ValueError(f"{label} must all have the same number of periods")
    bad = sorted(
        name
        for name, values in series.items()
        if not all(math.isfinite(v) and (v > -1.0 or not loss_floor) for v in values)
    )
    if bad:
        floor = " and above -1" if loss_floor else ""
        raise ValueError(f"{label} must be finite{floor}: {', '.join(bad)}")
    return lengths.pop()


class RiskInput(BaseModel):
    """A portfolio's weights, its assets' returns, and optionally factor returns."""

    portfolio_id: str = Field(min_length=1, description="Identifier of the portfolio")
    weights: dict[str, float] = Field(
        min_length=1, max_length=MAX_ASSETS, description="Weight per asset, e.g. 0.25 for 25%"
    )
    asset_returns: dict[str, list[float]] = Field(
        description="Simple returns per asset, one list per weighted asset, aligned by period"
    )
    factor_returns: dict[str, list[float]] | None = Field(
        default=None,
        max_length=MAX_FACTORS,
        description="Factor returns (e.g. market, value), over the same periods",
    )
    periods_per_year: int = Field(default=12, ge=1, le=366)

    @model_validator(mode="after")
    def _aligned(self) -> RiskInput:
        if set(self.weights) != set(self.asset_returns):
            missing = sorted(set(self.weights) - set(self.asset_returns))
            extra = sorted(set(self.asset_returns) - set(self.weights))
            raise ValueError(
                "weights and asset_returns must name the same assets"
                + (f"; no returns for {', '.join(missing)}" if missing else "")
                + (f"; no weight for {', '.join(extra)}" if extra else "")
            )
        if not all(math.isfinite(weight) for weight in self.weights.values()):
            raise ValueError("weights must be finite")
        periods = _check_series("asset_returns", self.asset_returns, loss_floor=True)
        if periods < 2:
            raise ValueError("asset_returns need at least 2 periods")
        if self.factor_returns is not None:
            if not self.factor_returns:
                raise ValueError("factor_returns, when given, must name at least one factor")
            if _check_series("factor_returns", self.factor_returns, loss_floor=False) != periods:
                raise ValueError("factor_returns must cover the same periods as asset_returns")
            if periods <= len(self.factor_returns) + 1:
                raise ValueError(
                    f"{len(self.factor_returns)} factors need more than "
                    f"{len(self.factor_returns) + 1} periods; got {periods}"
                )
        return self

    @property
    def periods(self) -> int:
        """The number of periods in every series."""
        return len(next(iter(self.asset_returns.values())))


class AssetRisk(BaseModel):
    """One asset's weight, volatility, and contributions to portfolio volatility."""

    weight: float
    volatility: float
    marginal_contribution: float | None
    component_contribution: float | None
    percent_contribution: float | None


class FactorRisk(BaseModel):
    """Factor exposures and how much of the portfolio's variance each factor explains."""

    exposures: dict[str, float]
    asset_exposures: dict[str, dict[str, float]]
    variance_shares: dict[str, float] | None
    specific_share: float | None
    r_squared: float | None


class RiskOutput(BaseModel):
    """Portfolio volatility, its decomposition by asset, and optional factor exposures."""

    portfolio_id: str
    periods: int
    periods_per_year: int
    volatility: float
    gross_exposure: float
    net_exposure: float
    diversification_ratio: float | None
    contributions: dict[str, AssetRisk]
    factors: FactorRisk | None
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class MetricOutput(BaseModel):
    """A metric value with its convention, warnings, and evidence."""

    metric: str
    value: float
    convention: str
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class PerformanceOutput(BaseModel):
    """Every performance metric of a series, with conventions, warnings, and evidence."""

    series_id: str
    periods: int
    periods_per_year: int
    metrics: dict[str, float | None]
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


def _sample_warnings(params: ReturnsInput) -> list[str]:
    if len(params.returns) < MIN_OBSERVATIONS:
        return [
            f"Only {len(params.returns)} observations for {params.series_id}; "
            f"results are unreliable below {MIN_OBSERVATIONS}."
        ]
    return []


def _computed(statement: str, params: ReturnsInput) -> Evidence:
    return Evidence(
        kind="computed",
        statement=statement,
        sources=[SourceRef(source_id=params.series_id, location=f"{len(params.returns)} periods")],
    )


@tool(
    name="analyze_performance",
    description=(
        "Performance of a returns series, from empyrical: annualised return and volatility, "
        "Sharpe, Sortino, Calmar, Omega, maximum drawdown, tail ratio, stability, value at risk, "
        "hit rate, and, with a benchmark, alpha, beta, active return, tracking error, and "
        "information ratio. Conventions are stated in the output."
    ),
)
def analyze_performance(params: PerformanceInput) -> PerformanceOutput:
    """Compute every metric of ``params.returns`` (and against ``params.benchmark``)."""
    returns, periods = params.returns, params.periods_per_year
    values: dict[str, float | None] = {
        "annualized_return": metrics.annualized_return(returns, periods),
        "annualized_volatility": metrics.annualized_volatility(returns, periods),
        "sharpe_ratio": metrics.sharpe_ratio(returns, periods, params.risk_free),
        "sortino_ratio": metrics.sortino_ratio(returns, periods),
        "calmar_ratio": metrics.calmar_ratio(returns, periods),
        "omega_ratio": metrics.omega_ratio(returns, periods),
        "max_drawdown": metrics.max_drawdown(returns),
        "tail_ratio": metrics.tail_ratio(returns),
        "stability": metrics.stability(returns),
        "value_at_risk": metrics.value_at_risk(returns),
        "hit_rate": metrics.hit_rate(returns),
    }
    warnings = _sample_warnings(params)
    if params.benchmark is not None:
        values |= metrics.benchmark_metrics(returns, params.benchmark, periods, params.risk_free)
    if values["sharpe_ratio"] is None:
        warnings.append(f"{params.series_id} does not vary, so its Sharpe ratio is undefined.")
    shown = {name: CONVENTIONS[name] for name in ("source", "returns", *values)}
    shown["periods_per_year"] = str(periods)
    evidence = [
        _computed(f"{_label(name)} of {params.series_id} is {_format(name, value)}.", params)
        for name, value in values.items()
        if value is not None
    ]
    return PerformanceOutput(
        series_id=params.series_id,
        periods=len(returns),
        periods_per_year=periods,
        metrics=values,
        conventions=shown,
        warnings=warnings,
        evidence=evidence,
    )


@tool(
    name="annualized_return",
    description="Geometric annualized return of a series of periodic simple returns.",
)
def annualized_return(params: ReturnsInput) -> MetricOutput:
    """Compound the returns and annualize by ``periods_per_year``."""
    value = metrics.annualized_return(params.returns, params.periods_per_year)
    assert value is not None  # finite returns above -1 compound to a finite growth
    return MetricOutput(
        metric="annualized_return",
        value=value,
        convention=f"geometric, {params.periods_per_year} periods per year",
        warnings=_sample_warnings(params),
        evidence=[_computed(f"Annualized return of {params.series_id} is {value:.2%}.", params)],
    )


@tool(
    name="max_drawdown",
    description="Largest peak-to-trough decline of cumulative wealth, as a negative fraction.",
)
def max_drawdown(params: ReturnsInput) -> MetricOutput:
    """Track cumulative wealth and its running peak."""
    value = metrics.max_drawdown(params.returns)
    assert value is not None  # finite returns above -1 give a finite drawdown
    return MetricOutput(
        metric="max_drawdown",
        value=value,
        convention=CONVENTIONS["max_drawdown"],
        warnings=_sample_warnings(params),
        evidence=[_computed(f"Maximum drawdown of {params.series_id} is {value:.2%}.", params)],
    )


@tool(
    name="analyze_risk",
    description=(
        "Risk of a portfolio from its weights and its assets' returns: annualised volatility, "
        "each asset's marginal, component, and percent contribution to it (Euler), gross and "
        "net exposure, and the diversification ratio. With factor returns, adds factor "
        "exposures (OLS betas) and each factor's share of the portfolio variance. "
        "Conventions are stated in the output."
    ),
    max_output_bytes=500_000,
)
def analyze_risk(params: RiskInput) -> RiskOutput:
    """Decompose the volatility of ``params.weights`` and, with factors, its exposures."""
    breakdown = risk.volatility_contributions(
        params.weights, params.asset_returns, params.periods_per_year
    )
    name, periods = params.portfolio_id, params.periods
    source = SourceRef(source_id=name, location=f"{len(params.weights)} assets, {periods} periods")
    warnings: list[str] = []
    if periods < MIN_OBSERVATIONS:
        warnings.append(
            f"Only {periods} observations for {name}; results are unreliable below "
            f"{MIN_OBSERVATIONS}."
        )
    if breakdown.volatility == 0.0:
        warnings.append(f"{name} does not vary, so its volatility contributions are undefined.")
    statements = [f"Annualised volatility of {name} is {breakdown.volatility:.2%}."]
    statements += [
        f"{asset} contributes {item.percent:.1%} of the volatility of {name}."
        for asset, item in breakdown.contributions.items()
        if item.percent is not None
    ]

    factors: FactorRisk | None = None
    if params.factor_returns is not None:
        found = risk.factor_exposures(params.weights, params.asset_returns, params.factor_returns)
        factors = FactorRisk(
            exposures=found.exposures,
            asset_exposures=found.asset_exposures,
            variance_shares=found.variance_shares,
            specific_share=found.specific_share,
            r_squared=found.r_squared,
        )
        if found.collinear:
            warnings.append(
                "The factors are linearly dependent, so individual exposures are not "
                "identified; drop or combine factors before reading them one by one."
            )
        statements += [
            f"Exposure of {name} to {factor} is {value:.2f}."
            for factor, value in found.exposures.items()
        ]
        if found.r_squared is not None:
            statements.append(
                f"The factors explain {found.r_squared:.1%} of the variance of {name}."
            )

    return RiskOutput(
        portfolio_id=name,
        periods=periods,
        periods_per_year=params.periods_per_year,
        volatility=breakdown.volatility,
        gross_exposure=breakdown.gross_exposure,
        net_exposure=breakdown.net_exposure,
        diversification_ratio=breakdown.diversification_ratio,
        contributions={
            asset: AssetRisk(
                weight=item.weight,
                volatility=item.volatility,
                marginal_contribution=item.marginal,
                component_contribution=item.component,
                percent_contribution=item.percent,
            )
            for asset, item in breakdown.contributions.items()
        },
        factors=factors,
        conventions={
            key: text
            for key, text in RISK_CONVENTIONS.items()
            if factors or key not in FACTOR_CONVENTIONS
        }
        | {"periods_per_year": str(params.periods_per_year)},
        warnings=warnings,
        evidence=[
            Evidence(kind="computed", statement=statement, sources=[source])
            for statement in statements
        ],
    )


TOOLS: list[ToolSpec[Any, Any]] = [
    analyze_performance,
    analyze_risk,
    annualized_return,
    max_drawdown,
]


def analytics_tools() -> ToolRegistry:
    """Return a registry with every analytics tool."""
    return ToolRegistry(TOOLS)


def _label(name: str) -> str:
    return name.replace("_", " ").capitalize()


RATIOS = frozenset(
    {
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "omega_ratio",
        "tail_ratio",
        "stability",
        "beta",
        "information_ratio",
    }
)


def _format(name: str, value: float) -> str:
    ratio = name in RATIOS
    return f"{value:.2f}" if ratio else f"{value:.2%}"
