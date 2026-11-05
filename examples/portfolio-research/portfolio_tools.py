"""The portfolio-research example's tools: two small metrics, each a typed, plain function.

The platform's full analytics are `quant_analytics.tools`. These show how little a tool needs:
typed input and output, evidence, and a function that stays directly callable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import ToolRegistry, tool
from quant_analytics import metrics
from quant_analytics.computed import CONVENTIONS, MIN_OBSERVATIONS

__all__ = [
    "MetricOutput",
    "ReturnsInput",
    "SeriesInput",
    "annualized_return",
    "build_registry",
    "max_drawdown",
]


class SeriesInput(BaseModel):
    """Periodic simple returns for one series."""

    series_id: str = Field(min_length=1, description="Identifier of the returns series")
    returns: list[float] = Field(min_length=2, description="Simple returns, e.g. 0.01 for 1%")

    @field_validator("returns")
    @classmethod
    def _above_total_loss(cls, values: list[float]) -> list[float]:
        if not all(-1.0 < value < float("inf") for value in values):
            raise ValueError("returns must be finite and above -1 (a loss of more than 100%)")
        return values


class ReturnsInput(SeriesInput):
    """A series and how often it is measured."""

    periods_per_year: int = Field(ge=1, le=366, description="252 daily, 52 weekly, 12 monthly")


class MetricOutput(BaseModel):
    """A metric value with its convention, warnings, and evidence."""

    metric: str
    value: float
    convention: str
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


def _warnings(params: SeriesInput) -> list[str]:
    if len(params.returns) < MIN_OBSERVATIONS:
        return [
            f"Only {len(params.returns)} observations for {params.series_id}; "
            f"results are unreliable below {MIN_OBSERVATIONS}."
        ]
    return []


def _computed(statement: str, params: SeriesInput) -> Evidence:
    source = SourceRef(source_id=params.series_id, location=f"{len(params.returns)} periods")
    return Evidence(kind="computed", statement=statement, sources=[source])


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
        warnings=_warnings(params),
        evidence=[_computed(f"Annualized return of {params.series_id} is {value:.2%}.", params)],
    )


@tool(
    name="max_drawdown",
    description="Largest peak-to-trough decline of cumulative wealth, as a negative fraction.",
)
def max_drawdown(params: SeriesInput) -> MetricOutput:
    """Track cumulative wealth and its running peak."""
    value = metrics.max_drawdown(params.returns)
    assert value is not None  # finite returns above -1 give a finite drawdown
    return MetricOutput(
        metric="max_drawdown",
        value=value,
        convention=CONVENTIONS["max_drawdown"],
        warnings=_warnings(params),
        evidence=[_computed(f"Maximum drawdown of {params.series_id} is {value:.2%}.", params)],
    )


def build_registry() -> ToolRegistry:
    """Return a registry containing the example tools."""
    return ToolRegistry([annualized_return, max_drawdown])
