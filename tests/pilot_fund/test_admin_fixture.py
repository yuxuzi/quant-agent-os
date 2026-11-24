"""The desk reconciliation's two input files: our daily returns and the administrator's workbook.

site/admin_fixture.py makes both from the warehouse, through the fund's own adapter. These tests
check the files' layout, the percent scale, and that a restated month is the only break, at the
size asked.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

openpyxl = pytest.importorskip("openpyxl")
pytest.importorskip("quant_toolbox")

from build_warehouse import build  # noqa: E402

from quant_toolbox.performance import monthly_returns  # noqa: E402

SITE = Path(__file__).resolve().parents[2] / "examples" / "pilot-fund" / "site"
RESTATED = dt.date(2025, 12, 31)  # the last month the warehouse holds
RESTATED_KEY = (2025, 12)


@pytest.fixture(scope="module")
def admin_fixture() -> Iterator[Any]:
    """The site's fixture module, importable from the site's own folder."""
    sys.path.insert(0, str(SITE))
    try:
        import admin_fixture as module

        yield module
    finally:
        sys.path.remove(str(SITE))
        sys.modules.pop("admin_fixture", None)
        sys.modules.pop("warehouse_store", None)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("warehouse") / "kestrel.db"
    build(path)
    return path


@pytest.fixture(scope="module")
def files(admin_fixture: Any, warehouse: Path, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Our returns, the administrator's clean workbook, and one restated in its last month."""
    out = tmp_path_factory.mktemp("admin")
    days = admin_fixture.daily_returns(warehouse, "eq_ls")
    admin_fixture.write_our_returns(out / "ours.csv", "eq_ls", days)
    admin_fixture.write_administrator_workbook(out / "clean.xlsx", "eq_ls", days)
    admin_fixture.write_administrator_workbook(
        out / "restated.xlsx", "eq_ls", days, restate={RESTATED_KEY: 9.0}
    )
    return out


def _ours(files: Path) -> tuple[list[dt.date], list[float]]:
    lines = (files / "ours.csv").read_text("utf-8").splitlines()
    assert lines[0] == "date,eq_ls"
    rows = [line.split(",") for line in lines[1:]]
    return [dt.date.fromisoformat(day) for day, _ in rows], [float(value) for _, value in rows]


def _reference(path: Path) -> tuple[list[dt.date], list[float]]:
    """Read the workbook as the app's `[reference]` does: title rows, header on row 5, percent."""
    sheet = openpyxl.load_workbook(path, data_only=True)["Performance"]
    titles = [sheet.cell(row=number, column=1).value for number in range(1, 5)]
    assert all(isinstance(title, str) and title for title in titles)
    assert [sheet.cell(row=5, column=1).value, sheet.cell(row=5, column=2).value] == [
        "Month end",
        "eq_ls",
    ]
    dates, values = [], []
    for row in range(6, sheet.max_row + 1):
        dates.append(sheet.cell(row=row, column=1).value)
        values.append(sheet.cell(row=row, column=2).value)
    return dates, values


def test_our_returns_are_the_desks_gross_daily_returns(files: Path, warehouse: Path) -> None:
    from warehouse_store import WarehouseDatasetStore

    dataset = WarehouseDatasetStore(warehouse).get("desk_returns_eq_ls")
    assert dataset.index is not None
    dates, values = _ours(files)
    assert len(dates) == len(dataset.index) - 1  # the first day has no return
    assert [day.isoformat() for day in dates] == dataset.index[1:]
    assert values == pytest.approx(dataset.columns["gross_return"][1:], abs=0, rel=0)


def test_the_administrators_workbook_is_in_percent_with_title_rows(files: Path) -> None:
    dates, values = _reference(files / "clean.xlsx")
    assert len(values) == 36 and all(isinstance(value, float) for value in values)
    assert all(round(value, 4) == value for value in values)  # four decimal places of a percent
    assert all(abs(value) < 10 for value in values)  # percent, not fractions of one
    assert all(isinstance(day, dt.datetime | dt.date) for day in dates)


def _differences(files: Path, workbook: str) -> dict[tuple[int, int], float]:
    """Ours minus the reference, in basis points, by month, as the app's reconcile computes it."""
    our_dates, our_values = _ours(files)
    ref_dates, ref_percent = _reference(files / workbook)
    ours = monthly_returns(our_dates, our_values)
    theirs = monthly_returns(
        [day if isinstance(day, dt.date) else day.date() for day in ref_dates],
        [value / 100.0 for value in ref_percent],
    )
    assert ours.keys() == theirs.keys()
    return {key: (ours[key] - theirs[key]) * 10_000.0 for key in sorted(ours)}


def test_a_clean_workbook_agrees_with_our_returns_in_every_month(files: Path) -> None:
    diffs = _differences(files, "clean.xlsx")
    assert len(diffs) == 36
    assert max(abs(value) for value in diffs.values()) < 0.01  # rounding, a hundredth of a bp


def test_a_restatement_of_nine_basis_points_is_the_only_break(files: Path) -> None:
    diffs = _differences(files, "restated.xlsx")
    assert diffs[RESTATED_KEY] == pytest.approx(-9.0, abs=0.01)  # ours below the restated figure
    others = [value for key, value in diffs.items() if key != RESTATED_KEY]
    assert len(others) == 35 and max(abs(value) for value in others) < 0.01


def test_the_same_warehouse_gives_the_same_files(
    admin_fixture: Any, warehouse: Path, tmp_path: Path
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        days = admin_fixture.daily_returns(warehouse, "eq_ls")
        admin_fixture.write_our_returns(folder / "ours.csv", "eq_ls", days)
    assert (first / "ours.csv").read_bytes() == (second / "ours.csv").read_bytes()
