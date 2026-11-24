# ruff: noqa: E501
"""Round 1 counterpart documents for Larchmere: the trustee's monthly workbook and the
administrator's equity statement, as Excel files the fund receives.

Both are written from an estate that `build_larchmere.py` built (its raw trustee CSVs and its SQL
file), so they carry the numbers the platform serves. The trustee's workbook has merged header
cells, two header rows, and a restatement of last month's OC test. The administrator's statement
has title rows, a merged month column, and a footnote, and it is one month shorter than the fund's
own file. The facility imports nothing of the platform.

Usage: uv run python build_larchmere_round1.py ESTATE_DIR OUT_DIR
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

PRIOR_MONTH = "2026-09"
REPORT_MONTH = "2026-10"
RESTATED_DEAL, RESTATED_TEST = "LARCH-2018-2", "oc_e"
RESTATED_VALUE_OFFSET = -0.0005  # the restated OC sits this far below the trigger, so it fails
PLANTED_BREAK_MONTH = date(2025, 6, 30)  # the administrator's figure here is 40 bp higher
PLANTED_BREAK_PERCENT = 0.40  # percentage points
EQUITY_BOOK = "Equity 2018-2"
EQUITY_DEAL = "LARCH-2018-2"
PRECISION = 4  # decimal places of the administrator's percent figures


def _trustee_rows(estate: Path, month: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((estate / "lake" / "raw" / "trustee" / month).glob("*.csv")):
        with path.open(newline="") as file:
            rows.extend(csv.DictReader(file))
    return rows


def restated_value(prior: dict[str, str]) -> float:
    """The restated OC test value: a shade below its trigger, so a passing test now fails."""
    return float(prior["trigger_level"]) + RESTATED_VALUE_OFFSET


def write_trustee_report(estate: Path, out: Path) -> Path:
    """Write the trustee's monthly report: two header rows, merged group cells, and a restatement."""
    current = _trustee_rows(estate, REPORT_MONTH)
    prior = {(row["deal_id"], row["test_name"]): row for row in _trustee_rows(estate, PRIOR_MONTH)}
    book = Workbook()
    sheet = book.active
    sheet.title = "Monthly report"
    sheet["A1"] = "Harbourview Corporate Trust: monthly CLO compliance report (fictional)"
    sheet["A2"] = "Report date 30 October 2026. Larchmere CLO 2018-2 and Larchmere CLO 2021-1."
    for cell in ("A1", "A2"):
        sheet[cell].font = Font(bold=cell == "A1")
    heads = {
        "A4": "Deal", "B4": "Test", "C4": "Current report", "E4": "Result",
        "F4": "Restated from the prior report", "C5": "Value", "D5": "Trigger",
        "F5": "Prior value", "G5": "Restated value", "H5": "Change",
    }  # fmt: skip
    for ref, text in heads.items():
        sheet[ref] = text
        sheet[ref].font = Font(bold=True)
        sheet[ref].alignment = Alignment(horizontal="center", wrap_text=True)
    for ref in ("A4:A5", "B4:B5", "E4:E5", "C4:D4", "F4:H4"):
        sheet.merge_cells(ref)
    row_number = 6
    for line in current:
        sheet.cell(row_number, 1, line["deal_id"])
        sheet.cell(row_number, 2, line["test_name"])
        sheet.cell(row_number, 3, float(line["reported_value"]))
        sheet.cell(row_number, 4, float(line["trigger_level"]))
        sheet.cell(row_number, 5, line["result"])
        key = (line["deal_id"], line["test_name"])
        if key == (RESTATED_DEAL, RESTATED_TEST):
            before = prior[key]
            after = round(restated_value(before), 6)
            sheet.cell(row_number, 6, float(before["reported_value"]))
            sheet.cell(row_number, 7, after)
            sheet.cell(row_number, 8, round(after - float(before["reported_value"]), 6))
        row_number += 1
    sheet.cell(row_number + 1, 1, (
        f"Restated: {RESTATED_DEAL} {RESTATED_TEST} for 30 September 2026 is restated to "
        f"{restated_value(prior[(RESTATED_DEAL, RESTATED_TEST)]):.6f}, below its trigger; "
        "the restated result is FAIL. The trustee's own test result, not ours."
    ))  # fmt: skip
    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = 14
    book.properties.created = datetime(2026, 10, 31, 9, 0)
    book.properties.creator = "Harbourview Corporate Trust (fictional)"
    out.parent.mkdir(parents=True, exist_ok=True)
    book.save(out)
    return out


def equity_returns(estate: Path) -> list[tuple[date, float]]:
    """The class equity's monthly return by report date, from the deal's SQL file."""
    with closing(sqlite3.connect(estate / "sql" / "clo.db")) as db:
        rows = db.execute(
            "SELECT report_date, monthly_return FROM equity_nav WHERE deal_id = ? "
            "AND monthly_return IS NOT NULL ORDER BY report_date",
            (EQUITY_DEAL,),
        ).fetchall()
    return [(date.fromisoformat(day), float(value)) for day, value in rows]


def write_fund_returns(estate: Path, out: Path) -> Path:
    """The fund's own copy: a date column and the equity's return as a fraction, for performance-report."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date", EQUITY_BOOK])
        for day, value in equity_returns(estate):
            writer.writerow([day.isoformat(), f"{value:.10f}"])
    return out


def write_administrator_statement(estate: Path, out: Path) -> Path:
    """The administrator's statement: title rows, two header rows, a footnote, in percent.

    It holds one month less than the fund's file (the statement lags a month), and one month
    differs by PLANTED_BREAK_PERCENT, so a reconciliation has one break and one month on one side.
    """
    series = equity_returns(estate)[:-1]
    book = Workbook()
    sheet = book.active
    sheet.title = "Statement"
    sheet["A1"] = (
        "Harbourview Fund Services: class equity statement, Larchmere CLO 2018-2 (fictional)"
    )
    sheet["A2"] = (
        "Monthly net return, percent of opening NAV. Unaudited; revisions supersede earlier statements."
    )
    sheet["A1"].font = Font(bold=True)
    sheet.merge_cells("A4:A5")
    sheet["A4"] = "Month end"
    sheet["B4"] = "Net return, %"
    sheet["B5"] = EQUITY_BOOK
    for ref in ("A4", "B4", "B5"):
        sheet[ref].font = Font(bold=True)
        sheet[ref].alignment = Alignment(horizontal="center")
    row_number = 6
    for day, value in series:
        percent = value * 100
        if day == PLANTED_BREAK_MONTH:
            percent += PLANTED_BREAK_PERCENT
        sheet.cell(row_number, 1, day)
        sheet.cell(row_number, 1).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 2, round(percent, PRECISION))
        row_number += 1
    sheet.cell(
        row_number + 1, 1, "Figures are unaudited. Revised figures supersede earlier statements."
    )
    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 18
    book.properties.created = datetime(2026, 10, 31, 9, 0)
    book.properties.creator = "Harbourview Fund Services (fictional)"
    out.parent.mkdir(parents=True, exist_ok=True)
    book.save(out)
    return out


def build(estate: Path, out: Path) -> dict[str, Path]:
    """Write the round's documents into ``out``; return their paths by name."""
    return {
        "trustee": write_trustee_report(estate, out / "trustee-report-2026-10.xlsx"),
        "fund_returns": write_fund_returns(estate, out / "equity-2018-2-returns.csv"),
        "administrator": write_administrator_statement(
            estate, out / "administrator-equity-statement.xlsx"
        ),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    written: dict[str, Any] = build(Path(argv[0]), Path(argv[1]))
    for name, path in written.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
