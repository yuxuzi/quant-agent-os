"""The analytics tools: `analyze_performance`, `analyze_risk`, `analyze_signal`, and `run_backtest`.

All four are typed and deterministic. The signal tools read frames by reference only, so they are
served only with a dataset store.

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

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quant_agent_os.data import Dataset, DatasetStore, infer_periods_per_year, parse_date
from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.registration import FrameRef
from quant_agent_os.tools import Permission, ToolRegistry, ToolSpec, tool
from quant_analytics.computed import (
    MAX_ASSETS,
    MAX_FACTORS,
    ModelRisk,
    PerformanceInput,
    PerformanceOutput,
    RiskInput,
    RiskOutput,
    carried_forward_warnings,
    historical_stress,
    model_risk,
    performance,
    portfolio_risk,
)
from quant_analytics.references import ColumnRef
from quant_analytics.signals import (
    BacktestOutput,
    Panel,
    SignalOutput,
    Weighting,
    analyze_signal,
    backtest,
    default_horizons,
    slow_signal,
)
from quant_toolbox.performance import backend as metrics_backend
from quant_toolbox.research import DELISTING_RETURN, TRADE_LAG
from quant_toolbox.risk import MEASURE_CONVENTIONS

PERFORMANCE_DESCRIPTION = (
    "Performance of a returns series, from empyrical: annualised return and volatility, "
    "Sharpe, Sortino, Calmar, Omega, maximum drawdown, tail ratio, stability, value at risk, "
    "hit rate, skewness, excess kurtosis, Sharpe standard error and probability, and, with a "
    "benchmark, alpha, beta, capture ratios, and information ratio. Conventions are stated "
    "in the output."
)
RISK_DESCRIPTION = (
    "Risk of a portfolio from its weights and its assets' returns: annualised volatility, "
    "each asset's marginal, component, and percent contribution to it (Euler), gross and "
    "net exposure, effective positions, Cornish-Fisher value at risk, and the "
    "diversification ratio. With factor returns, adds factor "
    "exposures (OLS betas) and each factor's share of the portfolio variance; with a vendor "
    "factor_model, volatility and shares under it. Daily dated returns add historical stress "
    "scenarios. Conventions are stated in the output."
)
WEIGHTS_BY_REFERENCE = " Weights held in a dataset go as weights_ref."
SIGNAL_DESCRIPTION = (
    "A signal's predictive power over instruments' returns: mean IC and rank IC, rank IC IR "
    "and HAC t-statistic per horizon, quantile mean returns and top-minus-bottom spread, and "
    "autocorrelation. Conventions are stated in the output."
)
BACKTEST_DESCRIPTION = (
    "Simulated net returns of a signal traded with a lag and costs on turnover, as "
    "dollar-neutral or long-only top-quantile weights: annualised return and volatility, "
    "Sharpe, maximum drawdown, hit rate, turnover, cost drag, and benchmark alpha and beta. "
    "A simulation, not advice."
)
STRESS_CONVENTION = MEASURE_CONVENTIONS["historical_stress"] + (
    "; only daily, dated returns; scenario windows are conventional US equity episodes"
)
MODEL_CONVENTION = MEASURE_CONVENTIONS["factor_risk"] + (
    "; covariances given per period are scaled to a year by the model's periods_per_year"
)
FRAMES = " Pass both frames as {dataset} from list_datasets: dates by instruments."
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


class WeightsRef(BaseModel):
    """Weights: a dated row of dates by assets, or a column of assets as rows."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    column: str | None = Field(default=None, description="Only for assets as rows")
    as_of: str | None = Field(default=None, description="Only for dates by assets. Default: last")


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
    weights: dict[str, float] | None = Field(default=None, min_length=1, max_length=MAX_ASSETS)
    weights_ref: WeightsRef | None = None
    asset_returns: dict[str, list[float]] | DatasetRef
    factor_returns: dict[str, list[float]] | ColumnsRef | None = None
    factor_model: FactorModelRef | None = None
    periods_per_year: int | None = PERIODS_FIELD

    @model_validator(mode="after")
    def _periods_for_values(self) -> RiskRequest:
        if (self.weights is None) == (self.weights_ref is None):
            raise ValueError(
                "Pass the weights one way: weights inline, as {asset: weight}, or weights_ref, "
                "as {dataset, column} or {dataset, as_of}; not both and not neither."
            )
        if isinstance(self.asset_returns, dict) and self.periods_per_year is None:
            raise ValueError(NEEDS_PERIODS)
        return self


SIGNAL_FRAME = Field(description="Signal, dates by instruments")
RETURNS_FRAME = Field(description="Simple returns, dates by instruments")
LAG_FIELD = Field(default=TRADE_LAG, ge=0, le=63, description="Bars from signal to trade")
DELISTING_FIELD = Field(
    default=DELISTING_RETURN,
    ge=-1.0,
    le=0.0,
    description="Earned once where returns stop early",
)


class SignalRequest(BaseModel):
    """A signal study."""

    signal: FrameRef = SIGNAL_FRAME
    returns: FrameRef = RETURNS_FRAME
    horizons: list[int] | None = Field(
        default=None,
        min_length=1,
        max_length=8,
        description="Forward bars; the first is summarised. Default by the dates' frequency: "
        "daily 1, 5, 21; weekly 1, 4, 13; monthly 1, 3, 12; quarterly 1, 2, 4",
    )
    quantiles: int = Field(default=5, ge=2, le=10)
    lag: int = LAG_FIELD
    delisting_return: float = DELISTING_FIELD

    @field_validator("horizons")
    @classmethod
    def _horizons(cls, values: list[int] | None) -> list[int] | None:
        if values is None:
            return None
        if not all(1 <= value <= 252 for value in values) or len(set(values)) != len(values):
            raise ValueError("horizons must be distinct numbers of bars from 1 to 252")
        return values


class BacktestRequest(BaseModel):
    """A signal backtest."""

    signal: FrameRef = SIGNAL_FRAME
    returns: FrameRef = RETURNS_FRAME
    cost_bps: float = Field(ge=0.0, le=1000.0, description="Per unit of one-way turnover")
    weighting: Weighting = "dollar_neutral"
    quantiles: int = Field(default=5, ge=1, le=20, description="long_only holds the top quantile")
    lag: int = LAG_FIELD
    rebalance_every: int = Field(default=1, ge=1, le=252)
    benchmark: ColumnRef | None = None
    periods_per_year: int | None = PERIODS_FIELD
    risk_free: float = Field(default=0.0, ge=-0.1, le=0.5, description="Annual risk-free rate")
    delisting_return: float = DELISTING_FIELD


class FactorModelRef(BaseModel):
    """A vendor's factor model, by dataset."""

    model_config = ConfigDict(extra="forbid")

    loadings: str = Field(min_length=1, description="Dataset: assets by factors")
    factor_covariance: str = Field(min_length=1, description="Dataset: factors by factors")
    specific_variance: ColumnRef = Field(description="A column with one row per asset")
    periods_per_year: int = Field(
        default=1, ge=1, le=366, description="Frequency of the variances; 1: annualised"
    )


class Aligned(NamedTuple):
    """Series over the same rows, those rows' dates when known, and what was left out."""

    series: dict[str, list[float]]
    dates: list[date] | None
    notes: list[str]


class FactorModel(NamedTuple):
    """A factor model's loadings, factor covariance, and specific variances, annualised."""

    exposures: dict[str, dict[str, float]]
    covariance: dict[str, dict[str, float]]
    specific: dict[str, float]


MIN_HISTORY = 0.9
"""An asset with returns on fewer than this share of the best-covered asset's dates is left out."""
LISTED_GAPS = 3
"""Up to this many series with missing rows are named one by one; more are summarised."""


def _names(names: list[str], limit: int = 10) -> str:
    return ", ".join(names[:limit]) + (" and others" if len(names) > limit else "")


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
        gaps = {
            f"{ref.dataset}.{ref.column}": count
            for name, ref in references.items()
            if (count := sum(math.isnan(columns[name][at]) for at in positions[name]))
        }
        if len(gaps) <= LISTED_GAPS:
            notes += [
                f"Left out {count} rows where {series} is missing; nothing was filled in."
                for series, count in gaps.items()
            ]
        else:
            worst = sorted(gaps, key=lambda series: -gaps[series])
            notes.append(
                f"Left out rows where any of {len(gaps)} series is missing, up to "
                f"{gaps[worst[0]]} rows for one: {_names(worst, 5)}; nothing was filled in."
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

    def with_history(
        self, dataset_id: str, weights: dict[str, float]
    ) -> tuple[dict[str, float], list[str]]:
        """Return the held assets with enough history in ``dataset_id``, and notes.

        An asset with values on fewer than ``MIN_HISTORY`` of the dates the best-covered held asset
        has is left out, so that one listing, delisting, or liquidation does not cut every other
        asset's window. Its weight is reported, not given to the others.
        """
        table = self.dataset(dataset_id)
        absent = [name for name in weights if name not in table.columns]
        if absent:
            raise ValueError(
                f"The weights name {len(absent)} assets the returns '{table.id}' have no column "
                f"for: {_names(absent)}."
            )
        counts = {
            name: sum(math.isfinite(value) for value in table.columns[name]) for name in weights
        }
        enough = MIN_HISTORY * max(counts.values(), default=0)
        short = [name for name, count in counts.items() if count < enough]
        if not short or len(short) == len(weights):
            return weights, []
        kept = {name: weight for name, weight in weights.items() if name not in short}
        left = sum(weights[name] for name in short)
        return kept, [
            f"Left out {len(short)} assets with returns on fewer than {MIN_HISTORY:.0%} of the "
            f"dates in '{table.id}': {_names(short)}. Their weight of {left:.2%} is not in the "
            "risk figures; the other assets keep their weights."
        ]

    def panel(self, signal: FrameRef, returns: FrameRef) -> tuple[Panel, list[str]]:
        """Return the signal and returns on their shared dates and instruments, and notes.

        Missing values stay missing, cell by cell; nothing is filled in.
        """
        left, right = self.dataset(signal.dataset), self.dataset(returns.dataset)
        left_names = [_checked(left, name) for name in signal.columns or left.columns]
        right_names = [_checked(right, name) for name in returns.columns or right.columns]
        shared = [name for name in left_names if name in set(right_names)]
        if not shared:
            raise ValueError(
                f"The signal ('{left.id}') and the returns ('{right.id}') share no instrument "
                "columns; name each instrument's column the same in both."
            )
        tables = [left] if left.id == right.id else [left, right]
        rows, dates, notes = _common_rows(tables)
        if dates is None:
            raise ValueError(
                f"Dataset '{left.id}' or '{right.id}' has no dates; a signal study needs one row "
                "per date in both (`qaos data add --index`)."
            )
        for table, names in ((left, left_names), (right, right_names)):
            dropped = [name for name in names if name not in shared]
            if dropped:
                shown = ", ".join(dropped[:10]) + (" and others" if len(dropped) > 10 else "")
                notes.append(
                    f"Left out {len(dropped)} columns of '{table.id}' with no match in the other "
                    f"frame: {shown}."
                )

        def frame(table: Dataset) -> Any:
            picked = rows[table.id]
            return np.column_stack(
                [np.asarray(table.columns[name], dtype=np.float64)[picked] for name in shared]
            )

        returns_values = frame(right)
        if (returns_values[np.isfinite(returns_values)] <= -1.0).any():
            raise ValueError(
                f"Returns in '{right.id}' must be above -1 (a loss of more than 100%); give them "
                "as fractions, 0.01 for 1%."
            )
        sources = [
            SourceRef(
                source_id=table.id,
                location=f"{len(rows[table.id])} rows, {len(shared)} columns, "
                f"sha256 {table.fingerprint[:12]}",
            )
            for table in tables
        ]
        return Panel(dates, shared, frame(left), returns_values, sources), notes

    def factor_model(
        self, model: FactorModelRef, assets: list[str]
    ) -> tuple[FactorModel, list[str]]:
        """Return the model's loadings, covariance, and specific variances, annualised, and notes.

        Raises ValueError, naming what is missing, when the three do not line up with each other
        or with ``assets``.
        """
        loadings = self._by_name(model.loadings, "asset")
        covariance = self._by_name(model.factor_covariance, "factor")
        specific_table = self.dataset(model.specific_variance.dataset)
        specific_values = specific_table.column(model.specific_variance.column)
        if not specific_table.index or specific_table.dates is not None:
            raise ValueError(
                f"Dataset '{specific_table.id}' needs one row per asset, named in its first column."
            )
        specific = dict(zip(specific_table.index, specific_values, strict=True))
        factors = list(covariance.columns)
        rows = covariance.index or []
        if sorted(rows) != sorted(factors):
            raise ValueError(
                f"The factor covariance '{covariance.id}' must have one row per column, named the "
                f"same; its rows are {_names(rows)} and its columns {_names(factors)}."
            )
        unknown = [name for name in loadings.columns if name not in factors]
        if unknown:
            raise ValueError(
                f"Loadings '{loadings.id}' name factors the covariance '{covariance.id}' has not: "
                f"{_names(unknown)}."
            )
        missing = [asset for asset in assets if asset not in specific]
        if missing:
            raise ValueError(
                f"The specific variance '{specific_table.id}' has no row for {_names(missing)}."
            )
        unloaded = [asset for asset in assets if asset not in set(loadings.index or [])]
        scale = float(model.periods_per_year)
        position = {name: at for at, name in enumerate(rows)}
        table: dict[str, dict[str, float]] = {}
        for left in factors:
            table[left] = {}
            for right in factors:
                value = covariance.columns[right][position[left]]
                if not math.isfinite(value):
                    raise ValueError(
                        f"The factor covariance '{covariance.id}' is missing {left}, {right}."
                    )
                table[left][right] = value * scale
        bad = [
            asset for asset in assets if not math.isfinite(specific[asset]) or specific[asset] < 0
        ]
        if bad:
            raise ValueError(
                f"Specific variances must be zero or more, and present, for {_names(bad)}."
            )
        at = {name: row for row, name in enumerate(loadings.index or [])}
        exposures: dict[str, dict[str, float]] = {}
        gaps = 0
        for asset in assets:
            exposures[asset] = {}
            if asset in unloaded:
                continue
            for factor, values in loadings.columns.items():
                value = values[at[asset]]
                if math.isfinite(value):
                    exposures[asset][factor] = value
                else:
                    gaps += 1
        notes = []
        if unloaded:
            notes.append(
                f"The loadings '{loadings.id}' have no row for {_names(unloaded)}; their risk is "
                "specific only."
            )
        if gaps:
            notes.append(f"{gaps} loadings of held assets are missing and count as 0.")
        return (
            FactorModel(exposures, table, {asset: specific[asset] * scale for asset in assets}),
            notes,
        )

    def weights(self, reference: WeightsRef) -> tuple[dict[str, float], str, list[str]]:
        """Return the weights ``reference`` names, where they came from, and notes.

        A dataset with dates is a frame of dates by assets: the row on ``as_of``, or the last
        row, gives the weights. One without is assets by columns: ``column`` gives them. A
        missing cell is an asset not held and is left out. Raises ValueError naming what to fix.
        """
        table = self.dataset(reference.dataset)
        dates = table.dates
        if dates is None:
            if not table.index:
                raise ValueError(
                    f"Dataset '{table.id}' needs one row per asset, named in its first column, or "
                    "one row per date and one column per asset."
                )
            if reference.as_of is not None:
                raise ValueError(
                    f"Dataset '{table.id}' has one row per asset and no dates, so as_of does not "
                    "apply; name the weights column instead."
                )
            if reference.column is None:
                raise ValueError(
                    f"Dataset '{table.id}' has one row per asset; name the weights column in "
                    f"weights_ref, one of {_names(list(table.columns))}."
                )
            cells = dict(zip(table.index, table.column(reference.column), strict=True))
            where = f"{table.id}.{reference.column}"
        else:
            if reference.column is not None:
                raise ValueError(
                    f"Dataset '{table.id}' has one row per date and one column per asset, so "
                    "column does not apply; pass as_of to pick a date, or leave it out for the "
                    "latest."
                )
            if reference.as_of is None:
                row, which = len(dates) - 1, ", the latest date"
            else:
                day = parse_date(reference.as_of)
                if day is None:
                    raise ValueError(f"as_of must be a date, such as {dates[-1].isoformat()}.")
                if day not in dates:
                    raise ValueError(
                        f"Dataset '{table.id}' has no row for {day.isoformat()}; its dates run "
                        f"from {dates[0].isoformat()} to {dates[-1].isoformat()}."
                    )
                row, which = dates.index(day), ""
            cells = {name: values[row] for name, values in table.columns.items()}
            where = f"{table.id} on {dates[row].isoformat()}{which}"
        weights = {name: value for name, value in cells.items() if math.isfinite(value)}
        if not weights:
            raise ValueError(f"The weights in {where} are all missing, so there is no portfolio.")
        if len(weights) > MAX_ASSETS:
            raise ValueError(
                f"The weights in {where} name {len(weights)} assets; the most is {MAX_ASSETS}."
            )
        skipped = len(cells) - len(weights)
        notes = (
            [f"Left out {skipped} assets with no weight in {where}; they are not held."]
            if skipped
            else []
        )
        return weights, where, notes

    def _by_name(self, dataset_id: str, row: str) -> Dataset:
        table = self.dataset(dataset_id)
        if not table.index or table.dates is not None:
            raise ValueError(f"Dataset '{dataset_id}' needs one row per {row}, named in its index.")
        return table

    def on_dates(self, reference: ColumnRef, dates: list[date]) -> list[float]:
        """Return the column ``reference`` names on each of ``dates``, NaN where it has none."""
        table = self.dataset(reference.dataset)
        values = table.column(reference.column)
        if table.dates is None:
            raise ValueError(f"Dataset '{table.id}' has no dates to line up with the backtest.")
        by_date = dict(zip(table.dates, values, strict=True))
        return [by_date.get(day, math.nan) for day in dates]


def _checked(table: Dataset, name: str) -> str:
    """Return ``name`` if ``table`` has that column, or raise naming its columns."""
    table.column(name)
    return name


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
            "aligned with another dataset. Add a date column (`qaos data add --index`), or put "
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
    """Return a registry of the analytics tools, reading from ``store``.

    Without a store, only `analyze_performance` and `analyze_risk`, on values; with one, also
    `analyze_signal` and `run_backtest`, which take frames by reference only.

    The metrics backend is chosen here, so importing empyrical, with pandas and scipy, happens when
    the server starts and not inside a first call's time limit.
    """
    metrics_backend()
    permissions = [] if store is None else [Permission.READ_LOCAL_DATA]
    by_reference = "" if store is None else BY_REFERENCE

    @tool(
        name="analyze_performance",
        description=PERFORMANCE_DESCRIPTION + by_reference,
        permissions=permissions,
        version="2.1.0",
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
        description=RISK_DESCRIPTION
        + by_reference.replace("{dataset, column}", "{dataset}")
        + ("" if store is None else WEIGHTS_BY_REFERENCE),
        permissions=permissions,
        max_output_bytes=500_000,
        version="3.0.0",
    )
    def analyze_risk(params: RiskRequest) -> RiskOutput:
        resolver = _Resolver(store)
        assets, factors = params.asset_returns, params.factor_returns
        dates: list[date] | None = None
        notes: list[str] = []
        weights_from: str | None = None
        if params.weights_ref is not None:
            weights, weights_from, notes = resolver.weights(params.weights_ref)
        elif params.weights is not None:
            weights = params.weights
        else:  # the request's validator refuses this
            raise ValueError("Pass weights or weights_ref.")
        if isinstance(assets, DatasetRef):
            if isinstance(factors, dict):
                raise ValueError(
                    "Pass assets and factors the same way: both values or both references."
                )
            weights, short = resolver.with_history(assets.dataset, weights)
            notes = [*notes, *short]
            references = {
                f"asset:{name}": ColumnRef(dataset=assets.dataset, column=name) for name in weights
            }
            if factors is not None:
                references |= {
                    f"factor:{name}": ColumnRef(dataset=factors.dataset, column=name)
                    for name in factors.columns
                }
            aligned = resolver.aligned(references)
            dates, notes = aligned.dates, [*notes, *aligned.notes]
            assets = {name: aligned.series[f"asset:{name}"] for name in weights}
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
            weights=weights,
            asset_returns=assets,
            factor_returns=factors,
            periods_per_year=periods,
        )
        output = portfolio_risk(resolved)
        carried = carried_forward_warnings(assets, dates)
        stress, stress_note, stress_evidence = historical_stress(
            params.portfolio_id, weights, assets, dates, periods
        )
        model: ModelRisk | None = None
        model_evidence: list[Evidence] = []
        model_notes: list[str] = []
        conventions = {**output.conventions, "periods_per_year": chosen}
        if weights_from is not None:
            conventions["weights"] = (
                f"From {weights_from}; the asset returns are used over every date given, not "
                "cut at the weights' date"
            )
        if stress is not None:
            conventions["historical_stress"] = STRESS_CONVENTION
        if params.factor_model is not None:
            loaded, model_notes = resolver.factor_model(params.factor_model, list(weights))
            model, model_evidence = model_risk(params.portfolio_id, weights, *loaded)
            conventions["factor_model"] = MODEL_CONVENTION
        return output.model_copy(
            update={
                "historical_stress": stress,
                "historical_stress_note": stress_note,
                "factor_model": model,
                "conventions": conventions,
                "warnings": [*notes, *disagreement, *output.warnings, *carried, *model_notes],
                "evidence": [*output.evidence, *stress_evidence, *model_evidence],
                "data_versions": resolver.versions(),
            }
        )

    tools: list[ToolSpec[Any, Any]] = [analyze_performance, analyze_risk]
    if store is None:
        return ToolRegistry(tools)

    @tool(
        name="analyze_signal",
        description=SIGNAL_DESCRIPTION + FRAMES,
        permissions=permissions,
        timeout_seconds=30.0,
        version="2.0.0",
    )
    def analyze_signal_tool(params: SignalRequest) -> SignalOutput:
        resolver = _Resolver(store)
        panel, notes = resolver.panel(params.signal, params.returns)
        horizons, chosen = params.horizons, "given"
        if horizons is None:
            horizons, frequency = default_horizons(infer_periods_per_year(panel.dates))
            chosen = f"from the dates' frequency ({frequency})"
        output = analyze_signal(
            panel,
            (params.signal.dataset, params.returns.dataset),
            horizons=horizons,
            quantiles=params.quantiles,
            lag=params.lag,
            delisting_return=params.delisting_return,
        )
        return output.model_copy(
            update={
                "conventions": {
                    **output.conventions,
                    "horizons": f"{', '.join(map(str, horizons))} bars, {chosen}",
                },
                "warnings": [*notes, *output.warnings, *slow_signal(panel, horizons)],
                "data_versions": resolver.versions(),
            }
        )

    @tool(
        name="run_backtest",
        description=BACKTEST_DESCRIPTION + FRAMES,
        permissions=permissions,
        timeout_seconds=30.0,
        version="1.0.0",
    )
    def run_backtest(params: BacktestRequest) -> BacktestOutput:
        resolver = _Resolver(store)
        panel, notes = resolver.panel(params.signal, params.returns)
        periods, chosen, disagreement = _frequency(params.periods_per_year, panel.dates)
        benchmark = (
            None if params.benchmark is None else resolver.on_dates(params.benchmark, panel.dates)
        )
        output = backtest(
            panel,
            (params.signal.dataset, params.returns.dataset),
            weighting=params.weighting,
            quantiles=params.quantiles,
            lag=params.lag,
            cost_bps=params.cost_bps,
            rebalance_every=params.rebalance_every,
            periods_per_year=periods,
            benchmark=benchmark,
            risk_free=params.risk_free,
            delisting_return=params.delisting_return,
        )
        return output.model_copy(
            update={
                "conventions": {**output.conventions, "periods_per_year": chosen},
                "warnings": [*notes, *disagreement, *output.warnings],
                "data_versions": resolver.versions(),
            }
        )

    return ToolRegistry([*tools, analyze_signal_tool, run_backtest])
