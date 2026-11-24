# ruff: noqa: E501
"""Round 1 counterpart documents for Sablecroft: the quarterly managers' NAV feed as the fund
receives it at the data cut, and the administrator's revised statement issued after the next quarter.

The quarterly managers (IC02, RA02, RA03) publish each quarter's final return a fixed number of days
after the quarter end (`published_date` in the administrator's table). The source has no earlier
print, so this writer adds the friction the round is about: at the cut, the last published quarter
is a preliminary print that the administrator revises in its next statement. The first print of
each of the last quarter's returns is the final plus a fixed error (`FIRST_PRINT_ERROR`), and the
revised statement carries the finals. Both documents are written from the estate that
`build_sablecroft.py` built. The facility imports nothing of the platform.

Usage: uv run python build_sablecroft_round1.py ESTATE_DIR OUT_DIR
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import Font

CUT = date(2026, 1, 10)  # the estate's data cut, as build_sablecroft.py sets it
REVISED_ISSUED = date(2026, 3, 16)  # the statement after the next quarter (its publication lag)
QUARTERLY = ("IC02", "RA02", "RA03")
# The first print minus the final, for the last quarter published by the cut.
FIRST_PRINT_ERROR = {"IC02": 0.0100, "RA02": -0.0060, "RA03": 0.0080}


def read_quarterly(estate: Path) -> list[dict[str, object]]:
    """The administrator's quarterly rows (one per manager and quarter end), from the lake."""
    rows: list[dict[str, object]] = []
    for path in sorted(
        (estate / "uc" / "sablecroft" / "curated" / "mgr_returns_q").glob("dt=*/*.parquet")
    ):
        for row in pq.read_table(path).to_pylist():
            rows.append(row)
    rows.sort(key=lambda row: (str(row["period_end"]), str(row["manager_id"])))
    return rows


def lag_days(row: dict[str, object]) -> int:
    """Days from the quarter end to publication, as the administrator published it."""
    period = date.fromisoformat(str(row["period_end"]))
    published = date.fromisoformat(str(row["published_date"]))
    return (published - period).days


def feed_at_cut(rows: list[dict[str, object]]) -> tuple[dict[str, dict[str, float]], str]:
    """The quarter ends published by the cut, as returns, with the last one a first print."""
    published = [row for row in rows if date.fromisoformat(str(row["published_date"])) <= CUT]
    last = max(str(row["period_end"]) for row in published)
    feed: dict[str, dict[str, float]] = {}
    for row in published:
        manager = str(row["manager_id"])
        period = str(row["period_end"])
        value = float(row["return_local"])  # type: ignore[arg-type]
        if period == last:
            value += FIRST_PRINT_ERROR[manager]
        feed.setdefault(period, {})[manager] = value
    return feed, last


def write_feed(feed: dict[str, dict[str, float]], out: Path) -> Path:
    """The fund's copy as received at the cut: a date column and one return per manager."""
    with out.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["date", *QUARTERLY])
        for period in sorted(feed):
            writer.writerow([period, *(f"{feed[period][m]:.10f}" for m in QUARTERLY)])
    return out


def write_revised(rows: list[dict[str, object]], out: Path) -> Path:
    """The administrator's revised statement: title rows, a header on row 4, and the finals."""
    finals: dict[str, dict[str, float]] = {}
    for row in rows:
        finals.setdefault(str(row["period_end"]), {})[str(row["manager_id"])] = float(
            row["return_local"]  # type: ignore[arg-type]
        )
    book = Workbook()
    sheet = book.active
    sheet.title = "Quarterly returns"
    sheet["A1"] = "Northgate Fund Services: quarterly returns, revised statement (fictional)"
    sheet["A2"] = (
        f"Issued {REVISED_ISSUED:%d %B %Y}. Revised figures supersede the preliminary statements."
    )
    sheet["A1"].font = Font(bold=True)
    for column, text in enumerate(("Quarter end", *QUARTERLY), start=1):
        sheet.cell(4, column, text).font = Font(bold=True)
    row_number = 5
    for period in sorted(finals):
        sheet.cell(row_number, 1, date.fromisoformat(period)).number_format = "yyyy-mm-dd"
        for column, manager in enumerate(QUARTERLY, start=2):
            sheet.cell(row_number, column, finals[period][manager])
        row_number += 1
    last = max(finals)
    sheet.cell(
        row_number + 1, 1, f"{last} is revised from its preliminary statement of 14 December 2025."
    )
    sheet.column_dimensions["A"].width = 14
    book.properties.created = datetime(2026, 3, 16, 9, 0)
    book.properties.creator = "Northgate Fund Services (fictional)"
    book.save(out)
    return out


def write_statement_log(rows: list[dict[str, object]], out: Path) -> Path:
    """When each quarter was published, and how many days after its quarter end."""
    with out.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["manager_id", "period_end", "published_date", "lag_days"])
        for row in rows:
            writer.writerow(
                [row["manager_id"], row["period_end"], row["published_date"], lag_days(row)]
            )
    return out


def build(estate: Path, out: Path) -> dict[str, Path]:
    """Write the round's documents into ``out``, and the planted answers beside them."""
    out.mkdir(parents=True, exist_ok=True)
    rows = read_quarterly(estate)
    feed, last = feed_at_cut(rows)
    answers = {
        "cut": CUT.isoformat(),
        "last_quarter_at_cut": last,
        "first_print_error": FIRST_PRINT_ERROR,
        "lags_days": sorted({lag_days(row) for row in rows}),
    }
    (out / "round1-answers.json").write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n")
    return {
        "feed": write_feed(feed, out / "quarterly-feed-at-cut.csv"),
        "revised": write_revised(rows, out / "administrator-revised-statement.xlsx"),
        "statement_log": write_statement_log(rows, out / "statement-log.csv"),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    for name, path in build(Path(argv[0]), Path(argv[1])).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
