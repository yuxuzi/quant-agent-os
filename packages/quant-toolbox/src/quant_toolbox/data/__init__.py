"""Data: tables, price panels, configuration files, checks on returns, and synthetic data.

A table is one file: a date column, then one numeric column per name, with missing values as NaN.
Signals, weights, returns, positions, and prices all use it, so one application's output is the
next one's input. `save_table` writes ``<file>.meta.json`` beside it, recording what made the
file and from what.

Optional leaves: `quant_toolbox.data.excel` reads workbooks (openpyxl, the ``excel`` extra), and
`quant_toolbox.data.frames` converts to and from pandas.
"""

from quant_toolbox._types import BoolArray, FloatArray
from quant_toolbox.data._calendar import business_days
from quant_toolbox.data._checks import (
    MIN_OBSERVATIONS,
    OUTLIER,
    PERCENT_LIKE,
    percent_like,
    return_notes,
    short_sample,
)
from quant_toolbox.data._config import ConfigError, dump_config, load_config, read_toml
from quant_toolbox.data._synthetic import demo_book, demo_panel, demo_returns, random_walk_panel
from quant_toolbox.data._tables import (
    Loader,
    Panel,
    Table,
    TableError,
    aligned,
    load_csv,
    load_table,
    meta_path,
    parse_date,
    read_meta,
    save_table,
)

__all__ = [
    "MIN_OBSERVATIONS",
    "OUTLIER",
    "PERCENT_LIKE",
    "BoolArray",
    "ConfigError",
    "FloatArray",
    "Loader",
    "Panel",
    "Table",
    "TableError",
    "aligned",
    "business_days",
    "demo_book",
    "demo_panel",
    "demo_returns",
    "dump_config",
    "load_config",
    "load_csv",
    "load_table",
    "meta_path",
    "parse_date",
    "percent_like",
    "random_walk_panel",
    "read_meta",
    "read_toml",
    "return_notes",
    "save_table",
    "short_sample",
]
