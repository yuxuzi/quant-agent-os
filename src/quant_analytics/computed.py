"""Typed, deterministic results over Quant Toolbox's `performance` and `risk` (decision 0026).

`quant_analytics.tools` resolves what a caller passes into these inputs. The numbers come from
`quant_toolbox`: through empyrical-reloaded when it is installed, or the toolbox's numpy formulas,
which give the same answers; ``conventions["source"]`` names which. Every output carries the
metric conventions, warnings for short samples, and ``computed`` evidence that names the series
it came from. An undefined metric, NaN in the toolbox, is None here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from quant_agent_os.evidence import Evidence, SourceRef
from quant_toolbox import data, report, risk
from quant_toolbox import performance as metrics

MIN_OBSERVATIONS = data.MIN_OBSERVATIONS
MAX_ASSETS = 500
MAX_FACTORS = 50
PERCENT_LIKE = data.PERCENT_LIKE
RISK_CONVENTIONS = risk.CONVENTIONS
FACTOR_CONVENTIONS = risk.FACTOR_CONVENTIONS
TAIL_CUTOFF = 0.05  # parametric value at risk and expected shortfall at 95%
# Conventions this package states itself, for measures the toolbox names differently.
EXTRA_CONVENTIONS = {
    "best_period": "the highest single-period return",
    "worst_period": "the lowest single-period return",
    "minimum_track_record": "periods of history needed for the probabilistic Sharpe ratio "
    "against 0 to reach 95%; none when the Sharpe ratio is not positive",
    "probabilistic_sharpe_ratio": "probability the true Sharpe ratio exceeds 0 "
    "(Bailey and Lopez de Prado, 2012)",
}
RISK_TAIL_CONVENTIONS = {
    "parametric_var": "95%, per period, negative for a loss, on the portfolio's returns "
    "(weights constant): mean plus the Cornish-Fisher quantile, adjusted for skew and excess "
    "kurtosis, times the sample deviation",
    "parametric_expected_shortfall": "95%, per period, Gaussian: mean minus deviation times "
    "phi(z) / 0.05, on the portfolio's returns",
    "herfindahl": risk.MEASURE_CONVENTIONS["herfindahl"],
}
METRIC_KINDS = {
    "best_period": "percent",
    "worst_period": "percent",
    "minimum_track_record": "count",
}


def __getattr__(name: str) -> object:
    """``CONVENTIONS``: every metric's convention, naming the backend that computes them now.

    Computed on use, so importing this module does not import empyrical.
    """
    if name == "CONVENTIONS":
        return metrics.conventions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ReturnsInput(BaseModel):
    """Periodic simple returns for one series."""

    series_id: str = Field(min_length=1, description="Identifier of the returns series")
    returns: list[float] = Field(min_length=2, description="Simple returns, e.g. 0.01 for 1%")
    periods_per_year: int = Field(ge=1, le=366, description="252 daily, 52 weekly, 12 monthly")

    @field_validator("returns")
    @classmethod
    def _finite(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) and value > -1.0 for value in values):
            raise ValueError(
                "returns must be finite and above -1 (a loss of more than 100%); give them as "
                "fractions, 0.01 for 1%"
            )
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
        floor = " and above -1 (fractions, 0.01 for 1%)" if loss_floor else ""
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
    periods_per_year: int = Field(ge=1, le=366, description="252 daily, 52 weekly, 12 monthly")

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


class ModelRisk(BaseModel):
    """Volatility under a vendor's factor model, split into factors, specific risk, and assets."""

    volatility: float
    factor_volatility: float
    specific_volatility: float
    exposures: dict[str, float]
    factor_shares: dict[str, float] | None
    specific_share: float | None
    contributions: dict[str, float] | None


class RiskOutput(BaseModel):
    """Portfolio volatility, its decomposition by asset, and optional factor exposures."""

    portfolio_id: str
    periods: int
    periods_per_year: int
    volatility: float
    gross_exposure: float
    net_exposure: float
    diversification_ratio: float | None
    parametric_var: float | None = None
    parametric_expected_shortfall: float | None = None
    herfindahl: float | None = None
    effective_positions: float | None = None
    contributions: dict[str, AssetRisk]
    factors: FactorRisk | None
    historical_stress: dict[str, float] | None = Field(
        default=None, description="Today's weights over each covered scenario window"
    )
    historical_stress_note: str | None = Field(
        default=None, description="Why scenarios are missing or not covered"
    )
    factor_model: ModelRisk | None = None
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


class PerformanceOutput(BaseModel):
    """Every performance metric of a series, with conventions, warnings, and evidence."""

    series_id: str
    periods: int
    periods_per_year: int
    metrics: dict[str, float | None]
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


def _sample_warnings(params: ReturnsInput) -> list[str]:
    return data.short_sample(params.series_id, len(params.returns))


def _unit_warnings(name: str, values: list[float]) -> list[str]:
    return data.percent_like(name, values)


def _defined(value: float) -> float | None:
    """The toolbox's NaN for an undefined metric becomes None in tool output."""
    return value if math.isfinite(value) else None


def _computed(statement: str, params: ReturnsInput) -> Evidence:
    return Evidence(
        kind="computed",
        statement=statement,
        sources=[SourceRef(source_id=params.series_id, location=f"{len(params.returns)} periods")],
    )


def performance(params: PerformanceInput) -> PerformanceOutput:
    """Compute every metric of ``params.returns`` (and against ``params.benchmark``)."""
    returns, periods = params.returns, params.periods_per_year
    computed = metrics.all_metrics(returns, periods, params.risk_free)
    warnings = _sample_warnings(params) + _unit_warnings(params.series_id, returns)
    if params.benchmark is not None:
        computed |= metrics.benchmark_metrics(returns, params.benchmark, periods, params.risk_free)
        captured = metrics.capture_ratios(returns, params.benchmark)
        computed |= {name: captured[name] for name in ("up_capture", "down_capture", "correlation")}
    computed |= _shape(returns, periods)
    values = {name: _defined(value) for name, value in computed.items()}
    if values["sharpe_ratio"] is None:
        warnings.append(f"{params.series_id} does not vary, so its Sharpe ratio is undefined.")
    conventions = metrics.conventions() | EXTRA_CONVENTIONS
    shown = {name: conventions[name] for name in ("source", "returns", *values)}
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


def _shape(returns: list[float], periods: int) -> dict[str, float]:
    """The distribution of ``returns`` and how far to trust their Sharpe ratio."""
    shape = metrics.return_distribution(returns)
    return {
        "skewness": shape.skewness,
        "excess_kurtosis": shape.excess_kurtosis,
        "best_period": shape.best,
        "worst_period": shape.worst,
        "sharpe_standard_error": metrics.sharpe_standard_error(returns, periods),
        "probabilistic_sharpe_ratio": metrics.probabilistic_sharpe_ratio(returns, periods),
        "minimum_track_record": metrics.minimum_track_record(returns, periods),
    }


def portfolio_risk(params: RiskInput) -> RiskOutput:
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
    for asset, values in params.asset_returns.items():
        warnings += _unit_warnings(asset, values)
    if breakdown.volatility == 0.0:
        warnings.append(
            f"{name} does not vary, or varies only by rounding (an exact hedge), so its "
            "volatility contributions are undefined."
        )
    book = [
        sum(params.weights[asset] * values[row] for asset, values in params.asset_returns.items())
        for row in range(periods)
    ]
    tail_var = _defined(metrics.parametric_var(book, TAIL_CUTOFF, method="cornish_fisher"))
    tail_es = _defined(metrics.parametric_expected_shortfall(book, TAIL_CUTOFF))
    size = risk.exposures(np.array(list(params.weights.values()), dtype=np.float64))
    concentrated = size.gross > 0.0
    statements = [f"Annualised volatility of {name} is {breakdown.volatility:.2%}."]
    if tail_var is not None:
        statements.append(f"95% Cornish-Fisher value at risk of {name} is {tail_var:.2%} a period.")
    if tail_es is not None:
        statements.append(f"95% Gaussian expected shortfall of {name} is {tail_es:.2%} a period.")
    if concentrated:
        statements.append(
            f"{name} has {size.effective_positions:.1f} effective positions "
            f"(Herfindahl {size.herfindahl:.3f})."
        )
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
        parametric_var=tail_var,
        parametric_expected_shortfall=tail_es,
        herfindahl=size.herfindahl if concentrated else None,
        effective_positions=size.effective_positions if concentrated else None,
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
        | RISK_TAIL_CONVENTIONS
        | {"periods_per_year": str(params.periods_per_year)},
        warnings=warnings,
        evidence=[
            Evidence(kind="computed", statement=statement, sources=[source])
            for statement in statements
        ],
    )


def _label(name: str) -> str:
    return report.label(name)


def _format(name: str, value: float) -> str:
    return report.format_metric(name, value, ratio_digits=2, kinds=METRIC_KINDS)


DAILY = frozenset({252, 365})
MODEL_STATEMENTS = 5  # the factors with the largest variance shares get a sentence each


def historical_stress(
    portfolio_id: str,
    weights: Mapping[str, float],
    asset_returns: Mapping[str, list[float]],
    dates: list[date] | None,
    periods_per_year: int,
) -> tuple[dict[str, float] | None, str | None, list[Evidence]]:
    """Today's ``weights`` over each covered `quant_toolbox.risk.SCENARIOS` window.

    Returns the covered scenarios' returns, a note on what is not covered, and evidence. The
    returns are None, with the reason, when there are no dates or they are not daily.
    """
    count = len(risk.SCENARIOS)
    if periods_per_year not in DAILY:
        return (
            None,
            "Historical stress needs daily returns; these are "
            f"{periods_per_year} a year, which cover no scenario window.",
            [],
        )
    if dates is None:
        return None, "Historical stress needs dated returns; these have no dates.", []
    names = list(weights)
    found = risk.historical_stress(
        np.array([weights[name] for name in names], dtype=np.float64),
        dates,
        np.column_stack([np.asarray(asset_returns[name], dtype=np.float64) for name in names]),
    )
    covered = {name: value for name, value in found.items() if value is not None}
    if not covered:
        return (
            None,
            f"The dates ({dates[0]} to {dates[-1]}) cover none of the {count} scenario windows "
            "without a gap.",
            [],
        )
    missing = count - len(covered)
    note = (
        f"{missing} of {count} scenario windows are not covered by the dates." if missing else None
    )
    source = SourceRef(source_id=portfolio_id, location=f"{len(dates)} dated periods")
    evidence = [
        Evidence(
            kind="computed",
            statement=f"Today's {portfolio_id} would have returned {value:.2%} over {scenario}.",
            sources=[source],
        )
        for scenario, value in covered.items()
    ]
    return covered, note, evidence


def model_risk(
    portfolio_id: str,
    weights: Mapping[str, float],
    loadings: Mapping[str, Mapping[str, float]],
    factor_covariance: Mapping[str, Mapping[str, float]],
    specific_variance: Mapping[str, float],
) -> tuple[ModelRisk, list[Evidence]]:
    """Volatility of ``weights`` under a supplied factor model, with its evidence.

    Covariance and specific variances are annualised. Raises ValueError when they do not line up.
    """
    found = risk.factor_risk(weights, loadings, factor_covariance, specific_variance)
    result = ModelRisk(
        volatility=found.volatility,
        factor_volatility=found.factor_volatility,
        specific_volatility=found.specific_volatility,
        exposures=found.exposures,
        factor_shares=found.factor_shares,
        specific_share=found.specific_share,
        contributions=found.contributions,
    )
    source = SourceRef(
        source_id=portfolio_id, location=f"{len(weights)} assets, {len(factor_covariance)} factors"
    )
    statements = [
        f"Annualised volatility of {portfolio_id} under the factor model is "
        f"{found.volatility:.2%}, of which factors {found.factor_volatility:.2%} and specific "
        f"{found.specific_volatility:.2%} alone."
    ]
    if found.factor_shares is not None:
        largest = sorted(found.factor_shares.items(), key=lambda item: -abs(item[1]))
        statements += [
            f"{factor} explains {share:.1%} of the model variance of {portfolio_id}."
            for factor, share in largest[:MODEL_STATEMENTS]
        ]
    return result, [
        Evidence(kind="computed", statement=statement, sources=[source]) for statement in statements
    ]
