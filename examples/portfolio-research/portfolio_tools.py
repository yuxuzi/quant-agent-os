"""Example deterministic analytics tools for the portfolio-research workflow.

In QuantFoundry these functions belong in the standalone portfolio analytics package; they live
here only to demonstrate the tool contract end to end on synthetic data.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.tools import ToolRegistry, tool

MIN_OBSERVATIONS = 12


class ReturnsInput(BaseModel):
    """Periodic simple returns for one series."""

    series_id: str = Field(min_length=1, description="Identifier of the returns series")
    returns: list[float] = Field(min_length=2, description="Simple returns, e.g. 0.01 for 1%")
    periods_per_year: int = Field(default=12, ge=1, le=366)


class MetricOutput(BaseModel):
    """A metric value with its convention, warnings, and evidence."""

    metric: str
    value: float
    convention: str
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
    name="annualized_return",
    description="Geometric annualized return of a series of periodic simple returns.",
)
def annualized_return(params: ReturnsInput) -> MetricOutput:
    """Compound the returns and annualize by ``periods_per_year``."""
    growth = math.prod(1 + value for value in params.returns)
    value = growth ** (params.periods_per_year / len(params.returns)) - 1
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
    wealth, peak, worst = 1.0, 1.0, 0.0
    for value in params.returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        worst = min(worst, wealth / peak - 1)
    return MetricOutput(
        metric="max_drawdown",
        value=worst,
        convention="peak-to-trough on compounded wealth, starting at 1.0",
        warnings=_sample_warnings(params),
        evidence=[_computed(f"Maximum drawdown of {params.series_id} is {worst:.2%}.", params)],
    )


def build_registry() -> ToolRegistry:
    """Return a registry containing the example tools."""
    return ToolRegistry([annualized_return, max_drawdown])
