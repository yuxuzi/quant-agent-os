"""Read a table from an Excel workbook. Needs openpyxl: the ``excel`` extra.

Importing this module never fails; reading a workbook without openpyxl raises
`quant_toolbox.MissingExtraError`, naming the extra to install.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import numpy as np

from quant_toolbox._optional import require
from quant_toolbox.data._tables import Table, TableError, parse_date


def read_workbook(path: Path, *, sheet: str = "", header_row: int = 1) -> Table:
    """Read one sheet: a header row with the date column first, then a row per date.

    The first row without a date ends the table, such as totals or footnotes below it; a date
    further down is an error, so that a mistyped date never cuts data off silently. Formulas are
    read as the values Excel last saved, and a workbook Excel never saved has none: those cells
    are missing, and the report's data notes count them.
    """
    openpyxl = require("openpyxl", purpose="reading Excel workbooks")
    if header_row < 1:
        raise TableError("header_row must be 1 or more.")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet and sheet not in workbook.sheetnames:
            raise TableError(
                f"{path.name} has no sheet {sheet!r}; it has {', '.join(workbook.sheetnames)}."
            )
        page = workbook[sheet] if sheet else workbook.worksheets[0]
        rows = list(page.iter_rows(min_row=header_row, values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise TableError(f"{path.name} has nothing at or below row {header_row}.")
    header = list(rows[0])
    while header and header[-1] is None:
        header.pop()
    if len(header) < 2 or any(name is None for name in header[1:]):
        raise TableError(
            f"{path.name}, row {header_row}: expected a header with a date column and named "
            "columns. Set header_row under [excel] in config.toml if the table starts lower."
        )
    columns = [str(name).strip() for name in header[1:]]
    dates: list[dt.date] = []
    values: list[list[float]] = []
    for offset, row in enumerate(rows[1:], header_row + 1):
        if not row or not _looks_like_date(row[0]):
            if not dates and row and row[0] is not None:
                parse_date(row[0])  # the first row must be a date: say what is wrong with it
            later = [item for item in rows[offset - header_row :] if item]
            if dates and any(_looks_like_date(item[0]) for item in later):
                raise TableError(
                    f"{path.name}, row {offset}: {row[0] if row else None!r} is not a date, but "
                    "dates follow it. Fix the cell, or write dates as real dates or YYYY-MM-DD."
                )
            break
        dates.append(parse_date(row[0]))
        values.append(
            [_number(row[i] if i < len(row) else None, path, offset) for i in range(1, len(header))]
        )
    if not dates:
        raise TableError(f"{path.name}: no rows of data below row {header_row}.")
    return Table(dates, columns, np.array(values, dtype=np.float64))


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, dt.date):
        return True
    try:
        parse_date(value)
    except TableError:
        return False
    return value is not None


def _number(value: Any, path: Path, row: int) -> float:
    if value is None or value == "":
        return math.nan
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TableError(f"{path.name}, row {row}: {value!r} is not a number.")
    return float(value)
