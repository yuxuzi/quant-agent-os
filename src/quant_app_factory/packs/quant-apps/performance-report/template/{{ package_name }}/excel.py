"""Excel: read a returns workbook, and write a report's tables as one workbook.

Cells hold real numbers with number formats, so people can work with them. Text that a spreadsheet
would run as a formula is written so that it stays text. Charts stay in the HTML report.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from .document import ChartBlock, Document, TableBlock, TextBlock
from .settings import ExcelSource
from .tables import Table, TableError, parse_date

_FORMATS = {"percent": "0.00%", "ratio": "0.00", "number": "#,##0"}
_ILLEGAL_SHEET = re.compile(r"[\[\]:*?/\\]")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def load_workbook_table(path: Path, source: ExcelSource | None = None) -> Table:
    """Read one sheet: a header row with the date column first, then a row per date.

    The first row without a date ends the table, such as totals or footnotes below it; a date
    further down is an error, so that a mistyped date never cuts data off silently. Formulas are
    read as the values Excel last saved, and a workbook Excel never saved has none: those cells
    are missing, and the report's data notes count them.
    """
    from openpyxl import load_workbook

    chosen = source or ExcelSource()
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if chosen.sheet and chosen.sheet not in workbook.sheetnames:
            raise TableError(
                f"{path.name} has no sheet {chosen.sheet!r}; it has "
                f"{', '.join(workbook.sheetnames)}."
            )
        sheet = workbook[chosen.sheet] if chosen.sheet else workbook.worksheets[0]
        rows = list(sheet.iter_rows(min_row=chosen.header_row, values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise TableError(f"{path.name} has nothing at or below row {chosen.header_row}.")
    header = list(rows[0])
    while header and header[-1] is None:
        header.pop()
    if len(header) < 2 or any(name is None for name in header[1:]):
        raise TableError(
            f"{path.name}, row {chosen.header_row}: expected a header with a date column and named "
            "columns. Set header_row under [excel] in config.toml if the table starts lower."
        )
    columns = [str(name).strip() for name in header[1:]]
    dates: list[dt.date] = []
    values: list[list[float]] = []
    for offset, row in enumerate(rows[1:], chosen.header_row + 1):
        if not row or not _looks_like_date(row[0]):
            if not dates and row and row[0] is not None:
                parse_date(row[0])  # the first row must be a date: say what is wrong with it
            later = [item for item in rows[offset - chosen.header_row :] if item]
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
        raise TableError(f"{path.name}: no rows of data below row {chosen.header_row}.")
    return Table(dates, columns, np.array(values, dtype=np.float64))


def write_workbook(document: Document, path: Path) -> Path:
    """Write each table to its own sheet, with notes under it, and return ``path``."""
    from openpyxl import Workbook

    workbook = Workbook()
    first = workbook.active
    first.title = "About"
    first.append([_safe(document.title)])
    if document.subtitle:
        first.append([_safe(document.subtitle)])
    used = {"About"}
    for block in document.blocks:
        if isinstance(block, ChartBlock):
            continue
        sheet = workbook.create_sheet(_sheet_name(block.title, used))
        if isinstance(block, TextBlock):
            for line in block.lines:
                sheet.append([_safe(line)])
        elif isinstance(block, TableBlock):
            sheet.append([_safe(column.name) for column in block.columns])
            for row in block.rows:
                sheet.append([_cell(value) for value in row])
            for number, column in enumerate(block.columns, 1):
                fmt = _FORMATS.get(column.kind)
                if fmt:
                    for row_number in range(2, len(block.rows) + 2):
                        sheet.cell(row=row_number, column=number).number_format = fmt
            if block.note:
                sheet.append([])
                sheet.append([_safe(block.note)])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


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


def _cell(value: Any) -> Any:
    if isinstance(value, str):
        return _safe(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe(text: str) -> str:
    """Keep text from running as a formula when the workbook is opened."""
    return "'" + text if text.startswith(_FORMULA_START) else text


def _sheet_name(title: str, used: set[str]) -> str:
    base = _ILLEGAL_SHEET.sub(" ", title).strip("' ")[:28] or "Sheet"
    name, number = base, 2
    while name.lower() in {item.lower() for item in used}:
        name, number = f"{base} {number}", number + 1
    used.add(name)
    return name
