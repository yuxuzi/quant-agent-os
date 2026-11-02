"""The analytics tools with series by reference: a dataset column instead of copied values.

``analyze_performance`` and ``analyze_risk`` here take either values or a reference such as
``{"dataset": "returns", "column": "fund"}``. A reference is resolved from the dataset store,
checked for alignment, and passed to the same computation as inline values, and the result
records each dataset's fingerprint as a data version. Reading datasets needs
``read_local_data``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quant_agent_os.data import Dataset, DatasetStore
from quant_agent_os.tools import Permission, ToolSpec, tool
from quant_analytics.tools import (
    MAX_ASSETS,
    MAX_FACTORS,
    PerformanceInput,
    PerformanceOutput,
    RiskInput,
    RiskOutput,
    analyze_performance,
    analyze_risk,
)

BY_REFERENCE = " Pass series as {dataset, column} from list_datasets rather than as values."


class ColumnRef(BaseModel):
    """One column of a dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    column: str = Field(min_length=1)


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
    periods_per_year: int = Field(default=12, ge=1, le=366)
    risk_free: float = Field(default=0.0, ge=-0.1, le=0.5, description="Annual risk-free rate")


class RiskRequest(BaseModel):
    """`RiskInput` with returns by value or by reference."""

    portfolio_id: str = Field(min_length=1)
    weights: dict[str, float] = Field(min_length=1, max_length=MAX_ASSETS)
    asset_returns: dict[str, list[float]] | DatasetRef
    factor_returns: dict[str, list[float]] | ColumnsRef | None = None
    periods_per_year: int = Field(default=12, ge=1, le=366)


class _Resolver:
    """Loads each referenced dataset once, records its version, and checks row alignment."""

    def __init__(self, store: DatasetStore) -> None:
        self.store = store
        self.loaded: dict[str, Dataset] = {}

    def dataset(self, dataset_id: str) -> Dataset:
        if dataset_id not in self.loaded:
            found = self.store.get(dataset_id)
            for other in self.loaded.values():
                if found.index and other.index and found.index != other.index:
                    raise ValueError(
                        f"Datasets '{other.id}' and '{found.id}' cover different rows; align "
                        "them on the same index before combining them."
                    )
            self.loaded[dataset_id] = found
        return self.loaded[dataset_id]

    def versions(self) -> dict[str, str]:
        return {f"dataset:{d.id}": d.fingerprint for d in self.loaded.values()}


def referenced_tools(store: DatasetStore) -> list[ToolSpec[Any, Any]]:
    """Return `analyze_performance` and `analyze_risk` taking series from ``store``."""

    @tool(
        name="analyze_performance",
        description=analyze_performance.description + BY_REFERENCE,
        permissions=[Permission.READ_LOCAL_DATA],
        max_output_bytes=analyze_performance.max_output_bytes,
    )
    def analyze_performance_by_reference(params: PerformanceRequest) -> PerformanceOutput:
        resolver = _Resolver(store)

        def series(value: list[float] | ColumnRef) -> list[float]:
            if isinstance(value, ColumnRef):
                return resolver.dataset(value.dataset).column(value.column)
            return value

        returns = series(params.returns)
        label = (
            f"{params.returns.dataset}.{params.returns.column}"
            if isinstance(params.returns, ColumnRef)
            else "series"
        )
        resolved = PerformanceInput(
            series_id=params.series_id or label,
            returns=returns,
            benchmark=series(params.benchmark) if params.benchmark is not None else None,
            periods_per_year=params.periods_per_year,
            risk_free=params.risk_free,
        )
        output = analyze_performance.function(resolved)
        return output.model_copy(update={"data_versions": resolver.versions()})

    @tool(
        name="analyze_risk",
        description=analyze_risk.description
        + BY_REFERENCE.replace("{dataset, column}", "{dataset}"),
        permissions=[Permission.READ_LOCAL_DATA],
        max_output_bytes=analyze_risk.max_output_bytes,
    )
    def analyze_risk_by_reference(params: RiskRequest) -> RiskOutput:
        resolver = _Resolver(store)
        assets = params.asset_returns
        if isinstance(assets, DatasetRef):
            table = resolver.dataset(assets.dataset)
            assets = {name: table.column(name) for name in params.weights}
        factors = params.factor_returns
        if isinstance(factors, ColumnsRef):
            table = resolver.dataset(factors.dataset)
            factors = {name: table.column(name) for name in factors.columns}
        resolved = RiskInput(
            portfolio_id=params.portfolio_id,
            weights=params.weights,
            asset_returns=assets,
            factor_returns=factors,
            periods_per_year=params.periods_per_year,
        )
        output = analyze_risk.function(resolved)
        return output.model_copy(update={"data_versions": resolver.versions()})

    return [analyze_performance_by_reference, analyze_risk_by_reference]
