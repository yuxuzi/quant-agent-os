"""Typed facades over kestrel_quant, drafted by `qaos legacy wrap`; edit them freely.

Each facade calls the legacy code unchanged and adds only what a tool needs: type
hints, a docstring, data by reference, quiet printing, and JSON-ready results. Keep
them thin: business logic stays in the legacy code.
"""

from __future__ import annotations

import contextlib
import dataclasses
import math
import sys
from collections.abc import Iterator
from typing import Any

import pandas as pd
from kestrel_quant.portfolio import backtest as legacy_backtest
from kestrel_quant.reports import desk_pnl_report as legacy_desk_pnl_report
from kestrel_quant.reports import drawdown_table as legacy_drawdown_table
from kestrel_quant.reports import summary as legacy_summary
from kestrel_quant.signals import carry_signal as legacy_carry_signal
from kestrel_quant.signals import momentum_signal as legacy_momentum_signal
from kestrel_quant.signals import value_zscore as legacy_value_zscore


# TODO(data): weights is guessed to be columns of one dataset, passed by reference as
#   pd.DataFrame; confirm its shape and unit.
# TODO(data): returns is guessed to be columns of one dataset, passed by reference as
#   pd.DataFrame; confirm its shape and unit.
# TODO(type): cost_bps's hint, int, is guessed from its default.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_backtest(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    cost_bps: int = 0,
) -> Any:
    """TODO(doc): say what kpc_backtest returns, in one sentence; clients read it.

    Args:
        weights: TODO(unit): what it holds, and in which unit.
        returns: TODO(unit): what it holds, and in which unit.
        cost_bps: TODO(unit): what it holds, and in which unit.
    """
    result = legacy_backtest(weights=weights, returns=returns, cost_bps=cost_bps)
    return _json_ready(result)


# TODO(data): returns is guessed to be one column, passed by reference as pd.Series;
#   confirm its shape and unit.
# TODO(type): top's hint, int, is guessed from its default.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_drawdown_table(returns: pd.Series, top: int = 5) -> Any:
    """The worst drawdowns of a daily return series: start, trough, recovery, depth, length.

    Args:
        returns: TODO(unit): what it holds, and in which unit.
        top: TODO(unit): what it holds, and in which unit.
    """
    result = legacy_drawdown_table(returns=returns, top=top)
    return _json_ready(result)


# TODO(type): desk has no hint in the legacy code; name its type.
# TODO(type): start has no hint in the legacy code; name its type.
# TODO(type): end has no hint in the legacy code; name its type.
# TODO(type): save's hint, bool, is guessed from its default.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_desk_pnl(
    desk: Any,
    start: Any = None,
    end: Any = None,
    save: bool = True,
) -> Any:
    """Daily P&L of a desk from end-of-day positions, net of commissions. Writes a CSV.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
        start: TODO(unit): what it holds, and in which unit.
        end: TODO(unit): what it holds, and in which unit.
        save: TODO(unit): what it holds, and in which unit.
    """
    # TODO(config): the legacy code reads module settings (_CACHE); add a parameter
    #   for each a caller may choose, and leave the rest the firm's.
    # TODO(config): reads the environment (CONFIG); set it where the server starts.
    # TODO(review): uses a database itself (pandas.read_sql_query, sqlite3.connect);
    #   prefer data by reference, or keep the permission declared in server.py.
    # TODO(review): writes files (.to_csv()) on the server; each call needs approval.
    #   Prefer returning the table for the caller to save.
    # TODO(review): keeps state between calls (_CACHE, _conn, global _conn); tools run
    #   on several threads at once.
    with _quiet():
        result = legacy_desk_pnl_report(desk=desk, start=start, end=end, save=save)
    return _json_ready(result)


# TODO(data): returns is guessed to be one column, passed by reference as pd.Series;
#   confirm its shape and unit.
# TODO(type): the return hint, dict[str, Any], is guessed from the return statements.
def kpc_summary(returns: pd.Series) -> dict[str, Any]:
    """TODO(doc): say what kpc_summary returns, in one sentence; clients read it.

    Args:
        returns: TODO(unit): what it holds, and in which unit.
    """
    result = legacy_summary(returns=returns)
    return _json_ready(result)


# TODO(data): prices is guessed to be columns of one dataset, passed by reference as
#   pd.DataFrame; confirm its shape and unit.
# TODO(type): lookback has no hint in the legacy code; name its type.
# TODO(type): skip has no hint in the legacy code; name its type.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_momentum_signal(
    prices: pd.DataFrame = None,
    lookback: Any = None,
    skip: Any = None,
) -> Any:
    """12-1 style price momentum: return from t-lookback to t-skip, z-scored across names.

    Args:
        prices: TODO(unit): what it holds, and in which unit.
        lookback: TODO(unit): what it holds, and in which unit.
        skip: TODO(unit): what it holds, and in which unit.
    """
    # TODO(config): the legacy code reads module settings (_CACHE); add a parameter
    #   for each a caller may choose, and leave the rest the firm's.
    # TODO(config): reads the environment (CONFIG); set it where the server starts.
    # TODO(review): uses a database itself (pandas.read_sql_query, sqlite3.connect);
    #   prefer data by reference, or keep the permission declared in server.py.
    # TODO(review): keeps state between calls (_CACHE, _conn, global _conn); tools run
    #   on several threads at once.
    with _quiet():
        result = legacy_momentum_signal(prices=prices, lookback=lookback, skip=skip)
    return _json_ready(result)


# TODO(type): asset_class has no hint in the legacy code; name its type.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_carry_signal(asset_class: Any = None) -> Any:
    """TODO(doc): say what kpc_carry_signal returns, in one sentence; clients read it.

    Args:
        asset_class: TODO(unit): what it holds, and in which unit.
    """
    # TODO(config): the legacy code reads module settings (_CACHE); add a parameter
    #   for each a caller may choose, and leave the rest the firm's.
    # TODO(config): reads the environment (CONFIG); set it where the server starts.
    # TODO(review): uses a database itself (pandas.read_sql_query, sqlite3.connect);
    #   prefer data by reference, or keep the permission declared in server.py.
    # TODO(review): keeps state between calls (_CACHE, _conn, global _conn); tools run
    #   on several threads at once.
    # TODO(type): the legacy code also takes **kwargs; add a named parameter for each
    #   one callers need.
    with _quiet():
        result = legacy_carry_signal(asset_class=asset_class)
    return _json_ready(result)


# TODO(data): values is guessed to be columns of one dataset, passed by reference as
#   pd.DataFrame; confirm its shape and unit.
# TODO(type): by has no hint in the legacy code; name its type.
# TODO(type): name the return type, such as float, a dataclass, or pd.Series; Any
#   gives clients no schema.
def kpc_value_zscore(values: pd.DataFrame = None, by: Any = None) -> Any:
    """Book-to-price z-scored within each region (or sector), equities only.

    Args:
        values: TODO(unit): what it holds, and in which unit.
        by: TODO(unit): what it holds, and in which unit.
    """
    # TODO(config): the legacy code reads module settings (_CACHE); add a parameter
    #   for each a caller may choose, and leave the rest the firm's.
    # TODO(config): reads the environment (CONFIG); set it where the server starts.
    # TODO(review): uses a database itself (pandas.read_sql_query, sqlite3.connect);
    #   prefer data by reference, or keep the permission declared in server.py.
    # TODO(review): keeps state between calls (_CACHE, _conn, global _conn); tools run
    #   on several threads at once.
    with _quiet():
        result = legacy_value_zscore(values=values, by=by)
    return _json_ready(result)


def _json_ready(value: Any) -> Any:
    """The legacy result as JSON values: tables and arrays as lists, missing values as null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_ready(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if hasattr(value, "columns") and hasattr(value, "index"):  # a pandas DataFrame
        return {
            "index": [_key(key) for key in value.index],
            "columns": {str(name): _json_ready(value[name].tolist()) for name in value.columns},
        }
    if hasattr(value, "items") and hasattr(value, "index"):  # a pandas Series
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "tolist"):  # a NumPy array or scalar
        return _json_ready(value.tolist())
    if hasattr(value, "model_dump") and not isinstance(value, type):  # a Pydantic model
        return _json_ready(value.model_dump())
    if hasattr(value, "isoformat"):  # a date or time
        return _key(value)
    if value is None or isinstance(value, str | int):
        return value
    raise TypeError(
        f"The legacy result holds a {type(value).__name__}, which JSON cannot carry; "
        "convert it to plain values in the facade."
    )


def _key(key: Any) -> str:
    if hasattr(key, "isoformat"):
        return str(key.isoformat()).removesuffix("T00:00:00")
    return str(key)


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """Send the legacy code's prints to stderr: over stdio, stdout carries the protocol."""
    with contextlib.redirect_stdout(sys.stderr):
        yield
