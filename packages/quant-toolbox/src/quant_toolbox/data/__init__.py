"""Data: tables, price panels, configuration files, checks on returns, and synthetic data.

A table is one file: a date column, then one numeric column per name, with missing values as NaN.
Signals, weights, returns, positions, and prices all use it, so one application's output is the
next one's input. `save_table` writes ``<file>.meta.json`` beside it, recording what made the
file and from what.

Optional leaves: `quant_toolbox.data.excel` reads workbooks (openpyxl, the ``excel`` extra),
`quant_toolbox.data.frames` converts to and from pandas, and `quant_toolbox.data.sql` reads a SQL
database through SQLAlchemy (the ``sql`` extra): `sql.connect` makes an engine, `fetch_rows` runs
SQL with binds, `pivot_long` turns long rows wide, and a `SqlDataset` declares one dataset.
"""

from quant_toolbox._types import BoolArray, FloatArray
from quant_toolbox.data import sql
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
from quant_toolbox.data.sql import (
    Rows,
    SqlDataset,
    SqlError,
    fetch_rows,
    pivot_long,
    rows_to_csv,
    shape_rows,
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
    "Rows",
    "SqlDataset",
    "SqlError",
    "Table",
    "TableError",
    "aligned",
    "business_days",
    "demo_book",
    "demo_panel",
    "demo_returns",
    "dump_config",
    "fetch_rows",
    "load_config",
    "load_csv",
    "load_table",
    "meta_path",
    "parse_date",
    "percent_like",
    "pivot_long",
    "random_walk_panel",
    "read_meta",
    "read_toml",
    "return_notes",
    "rows_to_csv",
    "save_table",
    "shape_rows",
    "short_sample",
    "sql",
]
