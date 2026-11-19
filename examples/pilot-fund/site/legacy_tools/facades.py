"""Typed facades over kestrel_quant, drafted by `qaos legacy wrap`; edit them freely.

Each facade calls the legacy code unchanged and adds only what a tool needs: type
hints, a docstring, data by reference, quiet printing, and JSON-ready results. Keep
them thin: business logic stays in the legacy code.

kestrel_quant reads the warehouse named by KPC_WAREHOUSE, which the server sets where it
starts, and keeps one connection and a query cache for the process. Its connection is
opened with check_same_thread=False and its cache is a plain dict, so a facade that
reads the warehouse itself holds _WAREHOUSE while it runs.
"""

from __future__ import annotations

import contextlib
import dataclasses
import math
import sys
import threading
from collections.abc import Iterator
from typing import Any

import pandas as pd
from kestrel_quant.config import CONFIG
from kestrel_quant.portfolio import backtest as legacy_backtest
from kestrel_quant.reports import desk_pnl_report as legacy_desk_pnl_report
from kestrel_quant.reports import drawdown_table as legacy_drawdown_table
from kestrel_quant.reports import summary as legacy_summary
from kestrel_quant.signals import carry_signal as legacy_carry_signal
from kestrel_quant.signals import momentum_signal as legacy_momentum_signal
from kestrel_quant.signals import value_zscore as legacy_value_zscore

from quant_agent_os.tools.access import AccessPolicy, note_refusal, permits

_WAREHOUSE = threading.Lock()
# Set by the fund's server: the access policy that decides which desks a caller sees.
_ACCESS: list[AccessPolicy | None] = [None]


class DeskRefusedError(LookupError):
    """Raised for a desk the caller may not see, or that does not exist: the same answer."""


def use_access(access: AccessPolicy | None) -> None:
    """Scope kpc_desk_pnl to the desks ``access`` lets each caller read positions of."""
    _ACCESS[0] = access


def kpc_backtest(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    cost_bps: float = 0.0,
) -> dict[str, float | None]:
    """Daily net returns of a book: yesterday's weights times today's returns, less costs.

    Costs are the day's one-way turnover (the sum of absolute weight changes) times
    cost_bps / 10,000. Returns are keyed by date, oldest first.

    Args:
        weights: Weights by date and instrument, as fractions of the book (1.0 is 100%).
        returns: Simple daily returns by date and instrument, as fractions.
        cost_bps: Cost per unit of one-way turnover, in basis points.
    """
    result = legacy_backtest(weights=weights, returns=returns, cost_bps=cost_bps)
    return _json_ready(result)  # type: ignore[no-any-return]


def kpc_drawdown_table(returns: pd.Series, top: int = 5) -> dict[str, Any]:
    """The worst drawdowns of a daily return series: start, trough, recovery, depth, length.

    Args:
        returns: Simple daily returns, as fractions; a missing day counts as zero.
        top: How many drawdowns to list, worst first.
    """
    result = legacy_drawdown_table(returns=returns, top=top)
    return _json_ready(result)  # type: ignore[no-any-return]


@dataclasses.dataclass
class DeskPnl:
    """A desk's P&L in USD over a window, from the morning report."""

    desk: str
    first_date: str | None
    last_date: str | None
    days: int
    gross_pnl: float | None
    commissions: float | None
    net_pnl: float | None
    daily_net_pnl: dict[str, float | None]


def kpc_desk_pnl(desk: str, start: str | None = None, end: str | None = None) -> DeskPnl:
    """A desk's daily P&L in USD from end-of-day positions and prices, net of commissions.

    Gross P&L is yesterday's quantities times today's price change; commissions are the
    day's trades'. The totals are sums over the window. A caller sees only the desks
    whose positions it may read.

    Args:
        desk: The desk: eq_ls, macro, or commod.
        start: First date, YYYY-MM-DD; the warehouse's first day when empty.
        end: Last date, YYYY-MM-DD; the warehouse's last day when empty.
    """
    visible = [name for name in CONFIG["desks"] if _may_see(name)]
    if desk in CONFIG["desks"] and desk not in visible:
        note_refusal("dataset.read", f"positions_{desk}")  # for the audit trail
    if desk not in visible:  # a refused desk answers exactly as an unknown one
        raise DeskRefusedError(f"No desk '{desk}'; the desks are {', '.join(visible) or 'none'}.")
    with _WAREHOUSE, _quiet():
        report = legacy_desk_pnl_report(desk=desk, start=start, end=end, save=False)
    totals = report[["gross_pnl", "commissions", "net_pnl"]].sum()
    return DeskPnl(
        desk=desk,
        first_date=str(report.index[0]) if len(report) else None,
        last_date=str(report.index[-1]) if len(report) else None,
        days=len(report),
        gross_pnl=_json_ready(float(totals["gross_pnl"])),
        commissions=_json_ready(float(totals["commissions"])),
        net_pnl=_json_ready(float(totals["net_pnl"])),
        daily_net_pnl=_json_ready(report["net_pnl"]),
    )


def _may_see(desk: str) -> bool:
    access = _ACCESS[0]
    return access is None or permits(access, "dataset.read", f"positions_{desk}")


def kpc_summary(returns: pd.Series) -> dict[str, float | None]:
    """Annualised return, volatility, and Sharpe ratio of a daily return series.

    Args:
        returns: Simple daily returns, as fractions.
    """
    result = legacy_summary(returns=returns)
    return _json_ready(result)  # type: ignore[no-any-return]


def kpc_momentum_signal(
    prices: pd.DataFrame,
    lookback: int | None = None,
    skip: int | None = None,
    as_of: str | None = None,
) -> dict[str, float | None]:
    """12-1 style price momentum on one date: return from t-lookback to t-skip, z-scored.

    Args:
        prices: Closing prices by date and instrument.
        lookback: Days back to the start of the window; the firm's 126 when empty.
        skip: Most recent days left out; the firm's 21 when empty.
        as_of: The date to score, YYYY-MM-DD; the last date when empty.
    """
    with _quiet():
        result = legacy_momentum_signal(prices=prices, lookback=lookback, skip=skip)
    return _cross_section(result, as_of)


def kpc_carry_signal(
    asset_class: str | None = None, as_of: str | None = None
) -> dict[str, float | None]:
    """The warehouse's carry signal on one date, z-scored across names, clipped at 3.

    Args:
        asset_class: Only this asset class (govt_bond_future, fx, credit_index,
            commodity_future); every carry instrument when empty.
        as_of: The date to score, YYYY-MM-DD; the last date when empty.
    """
    with _WAREHOUSE, _quiet():
        result = legacy_carry_signal(asset_class=asset_class)
    return _cross_section(result, as_of)


def kpc_value_zscore(
    values: pd.DataFrame, by: str | None = None, as_of: str | None = None
) -> dict[str, float | None]:
    """Book-to-price on one date, z-scored within each region (or sector), equities only.

    Args:
        values: Book-to-price by date and equity.
        by: Group by "region" or "sector"; the firm's "region" when empty.
        as_of: The date to score, YYYY-MM-DD; the last date when empty.
    """
    with _WAREHOUSE, _quiet():
        result = legacy_value_zscore(values=values, by=by)
    return _cross_section(result, as_of)


def _cross_section(frame: pd.DataFrame, as_of: str | None) -> dict[str, float | None]:
    """One date's row of a signal: a whole panel is too large to return to a client."""
    if frame.empty:
        raise ValueError("The signal has no dates; give it a longer price history.")
    if as_of is None:
        row = frame.iloc[-1]
    else:
        stamp = pd.Timestamp(as_of)
        if stamp not in frame.index:
            raise ValueError(
                f"No signal on {as_of}; the dates run from {_key(frame.index[0])} to "
                f"{_key(frame.index[-1])}, business days only."
            )
        row = frame.loc[stamp]
    return _json_ready(row)  # type: ignore[no-any-return]


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
