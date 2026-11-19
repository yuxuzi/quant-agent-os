"""Write a report's tables as one Excel workbook. Needs openpyxl: the ``excel`` extra.

Cells hold real numbers with number formats, so people can work with them. Text that a spreadsheet
would run as a formula is written so that it stays text. Charts stay in the HTML report.
Importing this module never fails; writing without openpyxl raises
`quant_toolbox.MissingExtraError`.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from quant_toolbox._optional import require
from quant_toolbox.report._document import ChartBlock, Document, TableBlock, TextBlock

_FORMATS = {"percent": "0.00%", "ratio": "0.00", "number": "#,##0"}
_ILLEGAL_SHEET = re.compile(r"[\[\]:*?/\\]")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def write_workbook(document: Document, path: Path) -> Path:
    """Write each table to its own sheet, with notes under it, and return ``path``."""
    openpyxl = require("openpyxl", purpose="writing Excel workbooks")
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "About"
    first.append([_safe(document.title)])
    if document.subtitle:
        first.append([_safe(document.subtitle)])
    if document.footer:
        first.append([])
        first.append([_safe(document.footer)])
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
