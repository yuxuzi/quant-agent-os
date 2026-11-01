"""Quant-Agent-OS tools over `quant_analytics.metrics`: typed, deterministic, and read-only.

Every output carries the metric conventions, warnings for short samples, and ``computed``
evidence that names the series it came from.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import ToolRegistry, ToolSpec, tool
from quant_analytics import metrics

MIN_OBSERVATIONS = 12
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


TOOLS: list[ToolSpec[Any, Any]] = [analyze_performance, annualized_return, max_drawdown]


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
