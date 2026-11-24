"""The fund administrator's monthly return workbook, as Tallis Fund Administration (fictional)
sends it.

Wrenfield's administrator values each book from its own price file, which has no row on a day the
US bond market is closed, and it compounds only the days it has. The fund's lake holds a row for
every weekday, so the fund's own monthly figure compounds the closed days too. The difference is
not planted: it is the holiday calendar, and `reconcile` shows it in the months the closed days
fall in. This module writes the administrator's file from plain rows, so it imports nothing of the
platform, and writing it twice gives the same bytes.

The closed days are the SIFMA full closures inside the estate's window (11 August to 31 December
2025): Labor Day, Columbus Day, Veterans Day, Thanksgiving, and Christmas. Good Friday (3 April
2026) and the early closes (such as the day after Thanksgiving) are not closures and are not here.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from openpyxl import Workbook

ADMINISTRATOR = "Tallis Fund Administration (fictional)"
TITLE_ROWS = (
    ADMINISTRATOR,
    "Wrenfield Credit Opportunities: monthly book returns, net, percent, to month end",
    "Books: hy_ls, ig_rv, distressed. Each book valued from its own price file.",
    "Source: administrator's monthly return file, version 1 (fixture).",
)
HEADER_ROW = 5
CLOSED_DAYS = frozenset(
    {"2025-09-01", "2025-10-13", "2025-11-11", "2025-11-27", "2025-12-25"}
)  # bond market full closures in the window
GENERATED = dt.datetime(2026, 1, 9, 9, 0, 0)  # fixed, so the file is the same every time


def compound_open_days(daily: Iterable[tuple[str, float]]) -> dict[str, tuple[str, float]]:
    """Return month -> (last day it has a return, the month's return over its open days)."""
    months: dict[str, tuple[str, float]] = {}
    for day, value in sorted(daily):
        if day in CLOSED_DAYS:
            continue
        month = day[:7]
        last, growth = months.get(month, (day, 1.0))
        months[month] = (max(last, day), growth * (1.0 + value))
    return {month: (last, growth - 1.0) for month, (last, growth) in months.items()}


def write_workbook(path: Path, columns: Mapping[str, Iterable[tuple[str, float]]]) -> None:
    """Write the administrator's workbook: the title rows, the header, then one row a month."""
    figures = {name: compound_open_days(daily) for name, daily in columns.items()}
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
        last = next(figures[name][month][0] for name in names if month in figures[name])
        sheet.cell(row=offset, column=1, value=last)
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
