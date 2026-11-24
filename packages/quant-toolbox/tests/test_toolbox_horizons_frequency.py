"""Horizons by the data's own frequency: a horizon shorter than one period is n/a (None).

Known answers are computed by hand in each test, not by the toolbox or empyrical.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_toolbox.performance import horizon_return, shorter_than_period


def _quarter_ends(first_year: int, quarters: int) -> list[dt.date]:
    ends = []
    for index in range(quarters):
        year, quarter = first_year + index // 4, index % 4
        month = 3 * (quarter + 1)
        last = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        ends.append(dt.date(year, month, last))
    return ends


def test_a_horizon_shorter_than_a_quarter_is_n_a_on_quarterly_data() -> None:
    dates = _quarter_ends(2023, 12)
    returns = np.full(12, 0.01)

    assert shorter_than_period("MTD", 4)
    assert horizon_return(dates, returns, "MTD", 4) is None
    assert horizon_return(dates, returns, "ITD", 4) is not None


def test_qtd_and_ytd_are_shown_on_quarterly_data() -> None:
    dates = _quarter_ends(2023, 12)  # 2023-03-31 .. 2025-12-31
    returns = np.full(12, 0.01)

    # QTD starts at 2025-09-30, so only 2025-12-31 is after it: 1.01 - 1.
    assert horizon_return(dates, returns, "QTD", 4) == pytest.approx(0.01)
    # YTD starts at 2024-12-31: the four quarters of 2025, compounded: 1.01 ** 4 - 1.
    assert horizon_return(dates, returns, "YTD", 4) == pytest.approx(1.01**4 - 1)


def test_mtd_qtd_are_n_a_on_annual_data_and_ytd_is_the_year() -> None:
    dates = [dt.date(2023, 12, 31), dt.date(2024, 12, 31), dt.date(2025, 12, 31)]
    returns = np.array([0.10, 0.20, 0.05])

    assert horizon_return(dates, returns, "MTD", 1) is None
    assert horizon_return(dates, returns, "QTD", 1) is None
    assert shorter_than_period("QTD", 1)
    # YTD starts at 2024-12-31, so only 2025 is after it: 5%.
    assert horizon_return(dates, returns, "YTD", 1) == pytest.approx(0.05)


def test_itd_on_annual_data_is_annualised_from_three_years() -> None:
    dates = [dt.date(2023, 12, 31), dt.date(2024, 12, 31), dt.date(2025, 12, 31)]
    returns = np.array([0.10, 0.20, 0.05])

    # Three annual returns compound to 1.1 * 1.2 * 1.05 = 1.386 over three years, so the
    # annualised figure is 1.386 ** (1 / 3) - 1, about 11.5% a year.
    assert horizon_return(dates, returns, "ITD", 1) == pytest.approx(1.386 ** (1 / 3) - 1)


def test_a_three_month_horizon_is_n_a_only_when_shorter_than_the_period() -> None:
    assert not shorter_than_period("3M", 4)
    assert shorter_than_period("3M", 1)
    assert not shorter_than_period("MTD", 12)
