"""Chart builders: each turns numbers into a `ChartBlock`, which a renderer draws later."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.performance import sharpe_ratio, underwater, wealth
from quant_toolbox.report._document import ChartBlock


def line_chart(
    title: str,
    dates: Sequence[dt.date],
    series: Mapping[str, Sequence[float] | FloatArray],
    *,
    note: str = "",
    percent: bool = False,
) -> ChartBlock:
    """Lines over dates, one per series; NaN leaves a gap."""
    return ChartBlock(
        title=title,
        dates=list(dates),
        series={name: [float(v) for v in values] for name, values in series.items()},
        note=note,
        percent=percent,
    )


def bar_chart(
    title: str,
    labels: Sequence[str],
    series: Mapping[str, Sequence[float] | FloatArray],
    *,
    note: str = "",
    percent: bool = False,
) -> ChartBlock:
    """Bars over named categories, one value per label in each series."""
    return ChartBlock(
        title=title,
        dates=[],
        series={name: [float(v) for v in values] for name, values in series.items()},
        note=note,
        kind="bar",
        labels=list(labels),
        percent=percent,
    )


def equity_curve(
    dates: Sequence[dt.date],
    returns: Mapping[str, Sequence[float] | FloatArray],
    *,
    title: str = "Growth of 1",
    note: str = "",
) -> ChartBlock:
    """Compounded wealth from 1 per series; a missing return leaves it unchanged."""
    series = {
        name: wealth(np.asarray(values, dtype=np.float64)) for name, values in returns.items()
    }
    return line_chart(title, dates, series, note=note)


def underwater_chart(
    dates: Sequence[dt.date],
    returns: Mapping[str, Sequence[float] | FloatArray],
    *,
    title: str = "Drawdown from peak",
    note: str = "",
) -> ChartBlock:
    """The drawdown from the running peak per series, as percentages."""
    series = {
        name: underwater(np.asarray(values, dtype=np.float64)) for name, values in returns.items()
    }
    return line_chart(title, dates, series, note=note, percent=True)


def rolling_sharpe_chart(
    dates: Sequence[dt.date],
    returns: Mapping[str, Sequence[float] | FloatArray],
    periods_per_year: int,
    *,
    window: int | None = None,
    risk_free: float = 0.0,
    title: str = "Rolling one-year Sharpe",
    note: str | None = None,
) -> ChartBlock:
    """Sharpe over a trailing ``window`` (one year of bars by default), blank until it fills."""
    size = window or periods_per_year
    series: dict[str, list[float]] = {}
    for name, values in returns.items():
        data = np.asarray(values, dtype=np.float64)
        rolled = [math.nan] * len(data)
        for end in range(size, len(data) + 1):
            rolled[end - 1] = sharpe_ratio(data[end - size : end], periods_per_year, risk_free)
        series[name] = rolled
    shown = note if note is not None else f"Over {size} bars; blank until a full window exists."
    return line_chart(title, dates, series, note=shown)
