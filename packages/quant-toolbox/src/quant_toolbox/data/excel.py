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
        raise TableError(_no_rows_message(path, header_row, rows))
    return Table(dates, columns, np.array(values, dtype=np.float64))


def _no_rows_message(path: Path, header_row: int, rows: list[tuple[Any, ...]]) -> str:
    """Say which row was read as the header, what it holds, and what the row below it holds."""
    header = list(rows[0])
    while header and header[-1] is None:
        header.pop()
    cells = ["empty" if cell is None else repr(cell) for cell in header[:6]]
    shown = ", ".join(cells) + (", ..." if len(header) > 6 else "")
    below = rows[1] if len(rows) > 1 else ()
    if all(cell is None for cell in below):
        seen = f"row {header_row + 1} is empty, which ends the table"
    elif below[0] is None:
        seen = f"row {header_row + 1} has no date in its first cell"
    else:
        try:
            parse_date(below[0])
        except TableError as error:  # parse_date says how to write the date
            seen = f"row {header_row + 1} starts with {below[0]!r}: {str(error).rstrip('.')}"
        else:
            seen = f"row {header_row + 1} starts with {below[0]!r}"
    dated = next(
        (
            offset
            for offset, row in enumerate(rows[1:], header_row + 1)
            if row and _looks_like_date(row[0])
        ),
        None,
    )
    where = f"The first date is in row {dated}." if dated else "No row below it starts with a date."
    return (
        f"{path.name}, row {header_row}: read as the column names ({shown}), but {seen}. {where} "
        "Set header_row under [excel] in config.toml to the row that names the columns."
    )


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
