"""One call for everything about a return series: metrics, benchmark, shape, trust, horizons.

`analyze` gathers the functions of this subpackage into one typed result, with the conventions
of every number and plain-sentence notes about the data, so an agent's tool or a tear sheet
makes one call. `Analysis.to_dict` gives JSON-safe data: NaN becomes None and dates ISO text.

The headline metrics are `all_metrics` unless the caller passes its own ``metrics``: any function
with the same signature, a `MetricsFunction`. One that adds to the standard set calls it:

    def house_metrics(returns, periods_per_year, risk_free=0.0, *, backend=None):
        standard = all_metrics(returns, periods_per_year, risk_free, backend=backend)
        return {**standard, "worst_month": worst_month(returns)}
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.data import as_calendar, configured_calendar, percent_like, short_sample
from quant_toolbox.performance._backend import backend as chosen_backend
from quant_toolbox.performance._conventions import conventions
from quant_toolbox.performance._distribution import Distribution, return_distribution
from quant_toolbox.performance._horizons import Drawdown, drawdowns, horizon_return
from quant_toolbox.performance._inference import (
    probabilistic_sharpe_ratio,
    sharpe_standard_error,
)
from quant_toolbox.performance._metrics import (
    Series,
    all_metrics,
    benchmark_metrics,
    capture_ratios,
)

HORIZONS = ("MTD", "QTD", "YTD", "1Y", "3Y", "5Y", "ITD")


class MetricsFunction(Protocol):
    """A set of headline metrics: `all_metrics`, or a firm's own with the same signature."""

    def __call__(
        self,
        returns: Series,
        periods_per_year: int,
        risk_free: float = 0.0,
        *,
        backend: str | None = None,
    ) -> dict[str, float]:
        """Return each metric's value by name."""
        ...


@dataclass(frozen=True)
class Analysis:
    """Every measure of one return series, with the conventions behind them and data notes."""

    as_of: dt.date
    periods_per_year: int
    observations: int
    metrics: dict[str, float]
    benchmark: dict[str, float] | None
    distribution: Distribution
    sharpe_inference: dict[str, float]
    horizons: dict[str, float | None]
    drawdowns: list[Drawdown]
    notes: list[str]
    conventions: dict[str, str]
    backend: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe data: NaN and infinities become None, dates become ISO text."""
        return {field.name: _plain(getattr(self, field.name)) for field in dataclasses.fields(self)}


def analyze(
    dates: Sequence[dt.date],
    returns: Series,
    periods_per_year: int,
    *,
    benchmark: Series | None = None,
    risk_free: float = 0.0,
    backend: str | None = None,
    horizons: Sequence[str] = HORIZONS,
    calendar: object = None,
    metrics: MetricsFunction | None = None,
) -> Analysis:
    """Metrics, benchmark comparison, distribution, Sharpe inference, horizons, and drawdowns.

    ``dates`` has one date per return, ascending. ``benchmark``, when given, has one return per
    date too; benchmark measures use the bars where both are present. ``calendar`` is anything
    `as_calendar` reads with no range; None uses the configured calendar, and the horizons use
    it, and the conventions name it, when one is set. ``metrics`` computes the headline metrics,
    `all_metrics` when None; the conventions name any other, whose own conventions are its
    author's to document.
    """
    chosen = as_calendar(calendar) if calendar is not None else configured_calendar()
    values = np.asarray(returns, dtype=np.float64).ravel()
    days = list(dates)
    if len(days) != values.size:
        raise ValueError(f"{len(days)} dates for {values.size} returns; give one date per return.")
    if not days:
        raise ValueError("No returns to analyze.")
    known = values[np.isfinite(values)]
    notes = short_sample("the returns", int(known.size)) + percent_like(
        "the returns", known.tolist()
    )
    compared = None
    if benchmark is not None:
        reference = np.asarray(benchmark, dtype=np.float64).ravel()
        if reference.shape != values.shape:
            raise ValueError("The benchmark needs one return per date, like the returns.")
        both = int((np.isfinite(values) & np.isfinite(reference)).sum())
        notes += short_sample("bars with both returns and benchmark", both)
        notes += percent_like("the benchmark", reference[np.isfinite(reference)].tolist())
        compared = {
            **benchmark_metrics(values, reference, periods_per_year, risk_free, backend=backend),
            **capture_ratios(values, reference),
        }
    excess = values - risk_free / periods_per_year
    return Analysis(
        as_of=days[-1],
        periods_per_year=periods_per_year,
        observations=int(known.size),
        metrics=_headline(metrics or all_metrics, values, periods_per_year, risk_free, backend),
        benchmark=compared,
        distribution=return_distribution(values),
        sharpe_inference={  # of excess returns, like metrics["sharpe_ratio"]
            "standard_error": sharpe_standard_error(excess, periods_per_year),
            "probabilistic_sharpe_ratio": probabilistic_sharpe_ratio(excess, periods_per_year),
        },
        horizons={
            label: horizon_return(days, values, label, periods_per_year, calendar=chosen)
            for label in horizons
        },
        drawdowns=drawdowns(days, values),
        notes=notes,
        conventions={
            **conventions(backend),
            **({} if chosen is None else {"calendar": chosen.name}),
            **({} if metrics in (None, all_metrics) else {"metrics": _name(metrics)}),
        },
        backend=chosen_backend(backend),
    )


def _headline(
    metrics: MetricsFunction,
    values: FloatArray,
    periods_per_year: int,
    risk_free: float,
    backend: str | None,
) -> dict[str, float]:
    """Run a metric set and check it returns numbers by name, so a report can show them."""
    result = metrics(values, periods_per_year, risk_free, backend=backend)
    if not isinstance(result, dict) or not all(isinstance(key, str) for key in result):
        raise TypeError(f"{_name(metrics)} must return a dict of metric name to number.")
    try:
        return {key: float(value) for key, value in result.items()}
    except (TypeError, ValueError) as error:
        raise TypeError(
            f"{_name(metrics)} returned a metric that is not a number: {error}"
        ) from error


def _name(function: object) -> str:
    module = getattr(function, "__module__", None)
    qualified = getattr(function, "__qualname__", None) or type(function).__name__
    return f"{module}.{qualified}" if module else qualified


def _plain(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dt.date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value
