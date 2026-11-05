"""The analytics tools: `analyze_performance` and `analyze_risk`, typed and deterministic.

They take either values or a reference such as ``{"dataset": "returns", "column": "fund"}``, so a
client names its data instead of copying it. References are resolved from the dataset store,
joined on their dates, and passed to the same computation as inline values, and the result
records each dataset's fingerprint as a data version. Reading datasets needs
``read_local_data``; without a store, the tools take values only and need no permissions.

Three things that would otherwise silently change a number are decided here and reported in the
output:
- **Frequency.** Without ``periods_per_year``, it comes from the dates; a value that disagrees
  with them is used, with a warning. With neither, the call is refused.
- **Alignment.** Series from different datasets are joined on their index, and the rows left
  out are counted. Without an index to join on, the call is refused.
- **Missing values.** A row with a missing value in any series used is left out and counted,
  and nothing is filled in.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_agent_os.data import Dataset, DatasetStore, infer_periods_per_year
from quant_agent_os.tools import Permission, ToolRegistry, tool
from quant_analytics.computed import (
    MAX_ASSETS,
    MAX_FACTORS,
    PerformanceInput,
    PerformanceOutput,
    RiskInput,
    RiskOutput,
    performance,
    portfolio_risk,
)
from quant_analytics.references import ColumnRef

PERFORMANCE_DESCRIPTION = (
    "Performance of a returns series, from empyrical: annualised return and volatility, "
    "Sharpe, Sortino, Calmar, Omega, maximum drawdown, tail ratio, stability, value at risk, "
    "hit rate, and, with a benchmark, alpha, beta, active return, tracking error, and "
    "information ratio. Conventions are stated in the output."
)
RISK_DESCRIPTION = (
    "Risk of a portfolio from its weights and its assets' returns: annualised volatility, "
    "each asset's marginal, component, and percent contribution to it (Euler), gross and "
    "net exposure, and the diversification ratio. With factor returns, adds factor "
    "exposures (OLS betas) and each factor's share of the portfolio variance. "
    "Conventions are stated in the output."
)
BY_REFERENCE = " Pass series as {dataset, column} from list_datasets rather than as values."
NEEDS_PERIODS = (
    "periods_per_year is needed for values given inline (252 daily, 52 weekly, 12 monthly)"
)
PERIODS_FIELD = Field(
    default=None,
    ge=1,
    le=366,
    description="Default: inferred from the dates. 252 daily, 52 weekly, 12 monthly",
)


class DatasetRef(BaseModel):
    """A dataset whose columns are named like the portfolio's assets."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)


class ColumnsRef(BaseModel):
    """Several columns of one dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    columns: list[str] = Field(min_length=1, max_length=MAX_FACTORS)


class PerformanceRequest(BaseModel):
    """`PerformanceInput` with series by value or by reference."""

    series_id: str | None = Field(default=None, description="Default: dataset.column")
    returns: list[float] | ColumnRef
    benchmark: list[float] | ColumnRef | None = None
    periods_per_year: int | None = PERIODS_FIELD
    risk_free: float = Field(default=0.0, ge=-0.1, le=0.5, description="Annual risk-free rate")

    @model_validator(mode="after")
    def _periods_for_values(self) -> PerformanceRequest:
        if isinstance(self.returns, list) and self.periods_per_year is None:
            raise ValueError(NEEDS_PERIODS)
        return self


class RiskRequest(BaseModel):
    """`RiskInput` with returns by value or by reference."""

    portfolio_id: str = Field(min_length=1)
    weights: dict[str, float] = Field(min_length=1, max_length=MAX_ASSETS)
    asset_returns: dict[str, list[float]] | DatasetRef
    factor_returns: dict[str, list[float]] | ColumnsRef | None = None
    periods_per_year: int | None = PERIODS_FIELD

    @model_validator(mode="after")
    def _periods_for_values(self) -> RiskRequest:
        if isinstance(self.asset_returns, dict) and self.periods_per_year is None:
            raise ValueError(NEEDS_PERIODS)
        return self


class Aligned(NamedTuple):
    """Series over the same rows, those rows' dates when known, and what was left out."""

    series: dict[str, list[float]]
    dates: list[date] | None
    notes: list[str]


class _Resolver:
    """Loads each referenced dataset once, records its version, and aligns the series used."""

    def __init__(self, store: DatasetStore | None) -> None:
        self.store = store
        self.loaded: dict[str, Dataset] = {}

    def dataset(self, dataset_id: str) -> Dataset:
        if self.store is None:
            raise ValueError("This server has no datasets; pass the values instead.")
        if dataset_id not in self.loaded:
            self.loaded[dataset_id] = self.store.get(dataset_id)
        return self.loaded[dataset_id]

    def versions(self) -> dict[str, str]:
        return {f"dataset:{d.id}": d.fingerprint for d in self.loaded.values()}

    def aligned(self, references: dict[str, ColumnRef]) -> Aligned:
        """Return the series ``references`` name, joined on their index, without missing rows."""
        tables = {ref.dataset: self.dataset(ref.dataset) for ref in references.values()}
        columns = {name: tables[ref.dataset].column(ref.column) for name, ref in references.items()}
        rows, dates, notes = _common_rows(list(tables.values()))
        positions = {name: rows[references[name].dataset] for name in references}
        kept = range(len(next(iter(positions.values()))))
        for name, ref in references.items():
            gaps = sum(math.isnan(columns[name][at]) for at in positions[name])
            if gaps:
                notes.append(
                    f"Left out {gaps} rows where {ref.dataset}.{ref.column} is missing; nothing "
                    "was filled in."
                )
        complete = [
            row
            for row in kept
            if not any(math.isnan(columns[name][positions[name][row]]) for name in references)
        ]
        if len(complete) < 2:
            raise ValueError(
                "Fewer than 2 rows have a value in every series used, so nothing can be computed."
            )
        return Aligned(
            {
                name: [columns[name][positions[name][row]] for row in complete]
                for name in references
            },
            [dates[row] for row in complete] if dates else None,
            notes,
        )


def _common_rows(
    tables: list[Dataset],
) -> tuple[dict[str, list[int]], list[date] | None, list[str]]:
    """Return each table's row numbers for the rows they share, their dates, and any notes."""
    first = tables[0]
    if len(tables) == 1:
        return {first.id: list(range(first.rows))}, first.dates, []
    undated = [table.id for table in tables if not table.index]
    if undated:
        raise ValueError(
            f"Dataset {', '.join(repr(name) for name in undated)} has no dates, so it cannot be "
            "aligned with another dataset. Add a date column (`dataset push --index`), or put "
            "the series in one file."
        )
    use_dates = all(table.dates is not None for table in tables)
    keys: dict[str, list[Any]] = {
        table.id: list(table.dates if use_dates and table.dates else table.index or [])
        for table in tables
    }
    shared = set.intersection(*(set(values) for values in keys.values()))
    if not shared:
        raise ValueError(
            f"Datasets {' and '.join(repr(table.id) for table in tables)} share no dates, so "
            "they cannot be aligned. Check that they cover the same period and date form."
        )
    rows = {
        name: [at for at, key in enumerate(values) if key in shared]
        for name, values in keys.items()
    }
    notes = [
        f"Aligned on {len(shared)} shared dates; left out {len(values) - len(shared)} rows of "
        f"'{name}'."
        for name, values in keys.items()
        if len(values) != len(shared)
    ]
    dates = [keys[first.id][at] for at in rows[first.id]] if use_dates else None
    return rows, dates, notes


def _frequency(supplied: int | None, dates: list[date] | None) -> tuple[int, str, list[str]]:
    """Return the periods per year, how it was chosen, and any warning about it."""
    inferred = infer_periods_per_year(dates) if dates else None
    if supplied is None:
        if inferred is None:
            raise ValueError(
                "The dates do not show a frequency; pass periods_per_year (252 daily, 52 weekly, "
                "12 monthly)."
            )
        return inferred, f"{inferred}, inferred from the dates", []
    if inferred is not None and inferred != supplied:
        return (
            supplied,
            f"{supplied}, as given",
            [
                f"periods_per_year is {supplied}, but the dates look like {inferred} a year; "
                "annualised figures use the value given."
            ],
        )
    return supplied, f"{supplied}, as given", []


def analytics_tools(store: DatasetStore | None = None) -> ToolRegistry:
    """Return a registry with `analyze_performance` and `analyze_risk`, reading from ``store``."""
    permissions = [] if store is None else [Permission.READ_LOCAL_DATA]
    by_reference = "" if store is None else BY_REFERENCE

    @tool(
        name="analyze_performance",
        description=PERFORMANCE_DESCRIPTION + by_reference,
        permissions=permissions,
        version="2.0.0",
    )
    def analyze_performance(params: PerformanceRequest) -> PerformanceOutput:
        resolver = _Resolver(store)
        if params.benchmark is not None and isinstance(params.returns, list) != isinstance(
            params.benchmark, list
        ):
            raise ValueError(
                "Pass returns and benchmark the same way: both values or both references."
            )
        if isinstance(params.returns, ColumnRef):
            references = {"returns": params.returns}
            if isinstance(params.benchmark, ColumnRef):
                references["benchmark"] = params.benchmark
            aligned = resolver.aligned(references)
            returns = aligned.series["returns"]
            benchmark = aligned.series.get("benchmark")
            dates, notes = aligned.dates, aligned.notes
            label = f"{params.returns.dataset}.{params.returns.column}"
        else:
            returns, label = params.returns, "series"
            benchmark = params.benchmark if isinstance(params.benchmark, list) else None
            dates, notes = None, []
        periods, chosen, disagreement = _frequency(params.periods_per_year, dates)
        resolved = PerformanceInput(
            series_id=params.series_id or label,
            returns=returns,
            benchmark=benchmark,
            periods_per_year=periods,
            risk_free=params.risk_free,
        )
        output = performance(resolved)
        return output.model_copy(
            update={
                "conventions": {**output.conventions, "periods_per_year": chosen},
                "warnings": [*notes, *disagreement, *output.warnings],
                "data_versions": resolver.versions(),
            }
        )

    @tool(
        name="analyze_risk",
        description=RISK_DESCRIPTION + by_reference.replace("{dataset, column}", "{dataset}"),
        permissions=permissions,
        max_output_bytes=500_000,
        version="2.0.0",
    )
    def analyze_risk(params: RiskRequest) -> RiskOutput:
        resolver = _Resolver(store)
        assets, factors = params.asset_returns, params.factor_returns
        dates: list[date] | None = None
        notes: list[str] = []
        if isinstance(assets, DatasetRef):
            if isinstance(factors, dict):
                raise ValueError(
                    "Pass assets and factors the same way: both values or both references."
                )
            references = {
                f"asset:{name}": ColumnRef(dataset=assets.dataset, column=name)
                for name in params.weights
            }
            if factors is not None:
                references |= {
                    f"factor:{name}": ColumnRef(dataset=factors.dataset, column=name)
                    for name in factors.columns
                }
            aligned = resolver.aligned(references)
            dates, notes = aligned.dates, aligned.notes
            assets = {name: aligned.series[f"asset:{name}"] for name in params.weights}
            factors = (
                {name: aligned.series[f"factor:{name}"] for name in factors.columns}
                if factors is not None
                else None
            )
        elif isinstance(factors, ColumnsRef):
            raise ValueError(
                "Pass assets and factors the same way: both values or both references."
            )
        periods, chosen, disagreement = _frequency(params.periods_per_year, dates)
        resolved = RiskInput(
            portfolio_id=params.portfolio_id,
            weights=params.weights,
            asset_returns=assets,
            factor_returns=factors,
            periods_per_year=periods,
        )
        output = portfolio_risk(resolved)
        return output.model_copy(
            update={
                "conventions": {**output.conventions, "periods_per_year": chosen},
                "warnings": [*notes, *disagreement, *output.warnings],
                "data_versions": resolver.versions(),
            }
        )

    return ToolRegistry([analyze_performance, analyze_risk])
