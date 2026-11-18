"""Warehouse access. One connection for the whole process."""

import sqlite3

import pandas as pd

from kestrel_quant.config import CONFIG, log

_conn = None
_CACHE = {}  # sql -> DataFrame, never expires; call clear_cache() after a reload


def get_conn():
    global _conn
    if _conn is None:
        log("connecting to", CONFIG["warehouse"])
        _conn = sqlite3.connect(CONFIG["warehouse"], check_same_thread=False)
    return _conn


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    clear_cache()


def clear_cache():
    _CACHE.clear()


def query(sql, *params, **kwargs):
    key = (sql, params)
    if key in _CACHE and not kwargs.get("fresh"):
        return _CACHE[key].copy()
    df = pd.read_sql_query(sql, get_conn(), params=params or None)
    _CACHE[key] = df
    return df.copy()


def load_returns(instruments=None, start=None, end=None):
    """Wide daily total returns, dates x instrument_id."""
    df = query("SELECT date, instrument_id, total_return FROM mkt_px_daily")
    wide = df.pivot(index="date", columns="instrument_id", values="total_return")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.iloc[1:]  # first day has no return
    if instruments is not None:
        wide = wide[list(instruments)]
    return wide.loc[start:end]


def load_prices(instruments=None):
    df = query("SELECT date, instrument_id, close FROM mkt_px_daily")
    wide = df.pivot(index="date", columns="instrument_id", values="close")
    wide.index = pd.to_datetime(wide.index)
    if instruments is not None:
        wide = wide[list(instruments)]
    return wide


def load_signal(name):
    df = query("SELECT date, instrument_id, value FROM sig_daily WHERE signal_name = ?", name)
    wide = df.pivot(index="date", columns="instrument_id", values="value")
    wide.index = pd.to_datetime(wide.index)
    return wide


def instruments(desk=None, asset_class=None):
    df = query("SELECT * FROM ref_instruments")
    if desk:
        df = df[df.desk == desk]
    if asset_class:
        df = df[df.asset_class == asset_class]
    return df.reset_index(drop=True)
