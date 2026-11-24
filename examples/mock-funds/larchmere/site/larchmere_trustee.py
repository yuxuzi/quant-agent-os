"""Larchmere's reader for the trustee's monthly workbook (round 1 of the 1.0 candidate).

The trustee sends Excel: a title, a group header row with merged cells, a second header row with
the column names, the tests, and a note. Its restatements sit in the same rows, under the
"Restated from the prior report" group. Nothing in the platform reads this file, so the fund's
reader checks the header layout and fails closed when it changes. The figures are numbers only;
PASS and FAIL stay text, which a dataset cannot serve (see FINDINGS, round 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

FIRST_DATA_ROW = 6
# (row, column) of each column name. Deal, Test, and Result are merged down two header rows, so
# their name sits in the first of the two.
EXPECTED_HEADERS = {
    (4, 1): "Deal", (4, 2): "Test", (5, 3): "Value", (5, 4): "Trigger",
    (4, 5): "Result", (5, 6): "Prior value",
}  # fmt: skip


class TrusteeLayoutError(ValueError):
    """The workbook's header row is not the layout the reader was written for."""


@dataclass(frozen=True)
class TrusteeLine:
    """One test in the trustee's report, and its restatement of the prior report when it has one."""

    deal_id: str
    test_name: str
    value: float
    trigger: float
    result: str
    prior_value: float | None = None
    restated_value: float | None = None

    @property
    def restated(self) -> bool:
        return self.restated_value is not None


@dataclass(frozen=True)
class TrusteeReport:
    """The tests of one trustee report, in the order the workbook gives them."""

    lines: list[TrusteeLine]

    def restatements(self) -> list[TrusteeLine]:
        """The tests the trustee restated from the prior report."""
        return [line for line in self.lines if line.restated]


def read_trustee_report(path: Path) -> TrusteeReport:
    """Read the first sheet: check its header, then one `TrusteeLine` per test row."""
    sheet = load_workbook(path, data_only=True).worksheets[0]
    for (row, column), name in EXPECTED_HEADERS.items():
        found = sheet.cell(row, column).value
        if found != name:
            raise TrusteeLayoutError(
                f"{path.name}: row {row}, column {column} is {found!r}, not {name!r}; "
                "the trustee's layout has changed, and the reader must be updated."
            )
    lines: list[TrusteeLine] = []
    for row in sheet.iter_rows(min_row=FIRST_DATA_ROW, values_only=True):
        deal, test = row[0], row[1]
        if deal is None or test is None:
            continue  # blank rows and the note under the table
        prior, restated = row[5], row[6]
        lines.append(
            TrusteeLine(
                deal_id=str(deal),
                test_name=str(test),
                value=float(row[2]),
                trigger=float(row[3]),
                result=str(row[4]),
                prior_value=None if prior is None else float(prior),
                restated_value=None if restated is None else float(restated),
            )
        )
    return TrusteeReport(lines)
