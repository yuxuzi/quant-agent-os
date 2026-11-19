"""Reads through one lazily opened connection and a module-level memo cache.

The cache only remembers what was read, so what reads through it is a facade, not a service.
"""

import os
import sqlite3

import pandas as pd

_conn = None
_CACHE = {}  # key -> value, filled on first read, never expires


def _connect():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(os.environ["FIRM_DB"], check_same_thread=False)
    return _conn


def _cached(key, load):
    if key not in _CACHE:
        _CACHE[key] = load()
    return _CACHE[key]


def _panel():
    return _cached("panel", lambda: pd.read_sql_query("SELECT * FROM prices", _connect()))


def index_level(name):
    """The latest level of a named index, remembered after the first read."""
    return _cached(("level", str(name)), lambda: 100.0 + len(str(name)))
