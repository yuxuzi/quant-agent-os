"""The empyrical backend: empyrical-reloaded, the metrics behind pyfolio (decision 0010).

Inputs arrive cleaned (finite values only, and series that vary where a metric divides by their
deviation), so empyrical and the numpy backend answer the same. Anything empyrical returns that
is not finite becomes NaN.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from quant_toolbox._optional import require
from quant_toolbox._types import FloatArray


def _call(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call an empyrical function; its divide-by-zero warnings become the NaN they produce."""
    empyrical = require("empyrical", extra="empyrical", purpose="the empyrical backend")
    with np.errstate(all="ignore"):
        return getattr(empyrical, name)(*args, **kwargs)


def _value(result: Any) -> float:
    number = float(result)
    return number if math.isfinite(number) else math.nan


def annualized_return(values: FloatArray, periods_per_year: int) -> float:
    if values.size == 0:
        return math.nan
    return _value(_call("annual_return", values, annualization=periods_per_year))


def annualized_volatility(values: FloatArray, periods_per_year: int) -> float:
    if values.size < 2:
        return math.nan
    return _value(_call("annual_volatility", values, annualization=periods_per_year))


def sharpe_ratio(values: FloatArray, periods_per_year: int, risk_free: float) -> float:
    return _value(
        _call(
            "sharpe_ratio",
            values,
            risk_free=risk_free / periods_per_year,
            annualization=periods_per_year,
        )
    )


def sortino_ratio(values: FloatArray, periods_per_year: int, required: float) -> float:
    if values.size == 0:
        return math.nan
    return _value(
        _call("sortino_ratio", values, required_return=required, annualization=periods_per_year)
    )


def max_drawdown(values: FloatArray) -> float:
    if values.size == 0:
        return math.nan
    return _value(_call("max_drawdown", values))


def calmar_ratio(values: FloatArray, periods_per_year: int) -> float:
    if values.size == 0:
        return math.nan
    return _value(_call("calmar_ratio", values, annualization=periods_per_year))


def omega_ratio(
    values: FloatArray, periods_per_year: int, risk_free: float, required: float
) -> float:
    return _value(
        _call(
            "omega_ratio",
            values,
            risk_free=risk_free,
            required_return=required,
            annualization=periods_per_year,
        )
    )


def tail_ratio(values: FloatArray) -> float:
    if values.size == 0:
        return math.nan
    return _value(_call("tail_ratio", values))


def stability(values: FloatArray) -> float:
    if values.size < 2:
        return math.nan
    return _value(_call("stability_of_timeseries", values))


def value_at_risk(values: FloatArray, cutoff: float) -> float:
    if values.size == 0:
        return math.nan
    return _value(_call("value_at_risk", values, cutoff=cutoff))


def beta(returns: FloatArray, market: FloatArray) -> float:
    return _value(_call("beta", returns, market))


def alpha(
    returns: FloatArray, market: FloatArray, periods_per_year: int, risk_free: float
) -> float:
    found, _ = _call(
        "alpha_beta",
        returns,
        market,
        risk_free=risk_free / periods_per_year,
        annualization=periods_per_year,
    )
    return _value(found)
