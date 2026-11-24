"""Two files the desk reconciliation reads, made from the warehouse through the fund's own adapter.

    python admin_fixture.py WAREHOUSE OUT_DIR [--desk eq_ls] [--restate YYYY-MM=BPS ...]

Writes, in OUT_DIR:

- ``<desk>_daily.csv``: the desk's daily gross returns as fractions, the values the server's
  ``desk_returns_<desk>`` dataset holds (the first day has no return, so it is left out).
- ``administrator_<desk>_monthly.xlsx``: the administrator's monthly returns in percent, from the
  same daily returns compounded within each month and rounded to four decimal places of a percent.
  It has four title rows, the header on row 5, and one row per month end, the layout a
  `[reference]` table with ``header_row = 5`` reads.

Each ``--restate YYYY-MM=BPS`` moves that month in the administrator's figures by BPS basis points,
as a restatement does. This is a fixture standing in for the file an administrator sends, not the
platform's figures. The same warehouse gives the same files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
from collections.abc import Sequence
from pathlib import Path

import openpyxl
from warehouse_store import WarehouseDatasetStore

HEADER_ROW = 5
DECIMALS = 4  # percent: 0.0001% is 0.01 basis points


def daily_returns(warehouse: Path, desk: str) -> list[tuple[dt.date, float]]:
    """Return the desk's daily gross returns, oldest first, without the first day's missing one."""
    dataset = WarehouseDatasetStore(warehouse).get(f"desk_returns_{desk}")
    if dataset.index is None:
        raise ValueError(f"desk_returns_{desk} has no dates")
    values = dataset.columns["gross_return"]
    return [
        (dt.date.fromisoformat(day), value)
        for day, value in zip(dataset.index, values, strict=True)
        if not math.isnan(value)
    ]


def monthly_percent(days: Sequence[tuple[dt.date, float]]) -> dict[tuple[int, int], float]:
    """Return each month's return, compounded from its daily returns, in percent (unrounded)."""
    growth: dict[tuple[int, int], float] = {}
    for day, value in days:
        key = (day.year, day.month)
        growth[key] = growth.get(key, 1.0) * (1.0 + value)
    return {key: (value - 1.0) * 100.0 for key, value in growth.items()}


def write_our_returns(path: Path, desk: str, days: Sequence[tuple[dt.date, float]]) -> None:
    """Write the daily returns as a CSV: a date column, then the desk's column, as fractions."""
    lines = [f"date,{desk}"] + [f"{day.isoformat()},{value!r}" for day, value in days]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def write_administrator_workbook(
    path: Path,
    desk: str,
    days: Sequence[tuple[dt.date, float]],
    restate: dict[tuple[int, int], float] | None = None,
) -> None:
    """Write the administrator's monthly workbook in percent, restated where ``restate`` says."""
    restated = restate or {}
    last_day: dict[tuple[int, int], dt.date] = {}
    for day, _ in days:
        last_day[(day.year, day.month)] = day
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Performance"
    titles = [
        "Kestrel Point Capital (fictional): monthly returns",
        f"{desk}, gross of commissions, percent",
        "Prepared by Example Fund Administration (fictional)",
        "Month-end values, rounded to four decimal places",
    ]
    for number, title in enumerate(titles, 1):
        sheet.cell(row=number, column=1, value=title)
    sheet.cell(row=HEADER_ROW, column=1, value="Month end")
    sheet.cell(row=HEADER_ROW, column=2, value=desk)
    for offset, (key, percent) in enumerate(sorted(monthly_percent(days).items()), 1):
        shift = restated.get(key, 0.0) / 100.0  # basis points to percent
        row = HEADER_ROW + offset
        sheet.cell(row=row, column=1, value=last_day[key])
        sheet.cell(row=row, column=2, value=round(percent, DECIMALS) + shift)
    workbook.save(path)


def parse_restatement(text: str) -> tuple[tuple[int, int], float]:
    """Read ``YYYY-MM=BPS``, such as ``2025-12=9``, into a month and its basis points."""
    month, _, bps = text.partition("=")
    year, _, number = month.partition("-")
    try:
        return (int(year), int(number)), float(bps)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM=BPS, not {text!r}") from error


def main(argv: Sequence[str] | None = None) -> None:
    """Write the two files for one desk, with any restatements applied."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("warehouse", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--desk", default="eq_ls")
    parser.add_argument("--restate", type=parse_restatement, action="append", default=[])
    args = parser.parse_args(argv)
    days = daily_returns(args.warehouse, args.desk)
    args.out.mkdir(parents=True, exist_ok=True)
    write_our_returns(args.out / f"{args.desk}_daily.csv", args.desk, days)
    write_administrator_workbook(
        args.out / f"administrator_{args.desk}_monthly.xlsx",
        args.desk,
        days,
        restate=dict(args.restate),
    )


if __name__ == "__main__":
    main()
