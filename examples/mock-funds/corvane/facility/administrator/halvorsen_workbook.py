"""The fund administrator's monthly return workbook, as Halvorsen Fund Services (fictional) sends
it.

Corvane's administrator keeps its own books and sends one workbook a month: a title block, then a
table of month-end dates and each composite's net return in percent. The fund reconciles its own
returns against it (`performance-report`'s `[reference]`). This module writes that workbook from
plain rows, so it is the administrator's file and not the platform's: it imports nothing of the
platform, and writing it twice gives the same bytes.

The administrator's one difference from the fund's books is planted and stated: it books the
tobacco spin-off's value on the ex-date, the fund books it on the record date, so March's LCC
figure is `SPIN_OFF_ADJUSTMENT_BP` basis points lower in the administrator's file.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from openpyxl import Workbook

ADMINISTRATOR = "Halvorsen Fund Services (fictional)"
TITLE_ROWS = (
    ADMINISTRATOR,
    "Corvane Equity Partners: monthly returns, net of management fee, percent, to month end",
    "Share class: institutional. Composites LCC and GQ. Generated from the administrator's books.",
    "Source: administrator's monthly return file, version 1 (fixture).",
)
HEADER_ROW = 5  # the table's header is the fifth row, so a reader starts there
SPIN_OFF_MONTH = "2026-03"
SPIN_OFF_ADJUSTMENT_BP = (
    -4.2
)  # on the month's growth factor: the reconciled difference reads about 4.1 bp,
#                             because the reduction is a share of the month's (1 + return)
GENERATED = dt.datetime(2026, 6, 3, 9, 0, 0)  # fixed, so the file is the same every time


def compound(daily: Iterable[tuple[str, float]]) -> dict[str, tuple[str, float]]:
    """Return month -> (last date in the month, the month's compounded return as a fraction)."""
    months: dict[str, tuple[str, float]] = {}
    for day, value in sorted(daily):
        month = day[:7]
        last, growth = months.get(month, (day, 1.0))
        months[month] = (max(last, day), growth * (1.0 + value))
    return {month: (last, growth - 1.0) for month, (last, growth) in months.items()}


def administrator_months(
    columns: Mapping[str, Iterable[tuple[str, float]]],
) -> dict[str, dict[str, tuple[str, float]]]:
    """Return, per composite, the administrator's month-end figures, with the planted spin-off
    adjustment applied to its month (LCC only: the spin-off was held by LCC's book)."""
    figures: dict[str, dict[str, tuple[str, float]]] = {}
    for name, daily in columns.items():
        months = compound(daily)
        if name == "LCC" and SPIN_OFF_MONTH in months:
            last, value = months[SPIN_OFF_MONTH]
            months[SPIN_OFF_MONTH] = (
                last,
                (1.0 + value) * (1.0 + SPIN_OFF_ADJUSTMENT_BP / 1e4) - 1.0,
            )
        figures[name] = months
    return figures


def write_workbook(path: Path, columns: Mapping[str, Iterable[tuple[str, float]]]) -> None:
    """Write the administrator's workbook: the title rows, the header, then one row a month."""
    figures = administrator_months(columns)
    names = list(figures)
    months = sorted({month for series in figures.values() for month in series})
    book = Workbook()
    book.properties.created = GENERATED
    book.properties.modified = GENERATED
    book.properties.creator = ADMINISTRATOR
    sheet = book.active
    sheet.title = "Monthly returns"
    for number, text in enumerate(TITLE_ROWS, start=1):
        sheet.cell(row=number, column=1, value=text)
    sheet.cell(row=HEADER_ROW, column=1, value="Month end")
    for column, name in enumerate(names, start=2):
        sheet.cell(row=HEADER_ROW, column=column, value=name)
    for offset, month in enumerate(months, start=HEADER_ROW + 1):
        sheet.cell(row=offset, column=1, value=figures[names[0]][month][0])
        for column, name in enumerate(names, start=2):
            entry = figures[name].get(month)
            value = None if entry is None else round(entry[1] * 100, 6)  # percent, 6 places
            sheet.cell(row=offset, column=column, value=value)
    book.save(path)
    _pin_entry_times(path)


def _pin_entry_times(path: Path) -> None:
    """Stamp the file with GENERATED: openpyxl stamps the time of saving on each entry and as
    the workbook's modified time."""
    with zipfile.ZipFile(path) as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
    stamp = GENERATED.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in entries:
            pinned = zipfile.ZipInfo(info.filename, date_time=GENERATED.timetuple()[:6])
            pinned.compress_type = zipfile.ZIP_DEFLATED
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + stamp, data)
            target.writestr(pinned, data)
    path.write_bytes(buffer.getvalue())
