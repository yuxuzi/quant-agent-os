"""Frequency inference, stated-count checks, and staleness from dates."""

from __future__ import annotations

import calendar
import datetime as dt

import pytest

from quant_toolbox.data import (
    Frequency,
    TableError,
    check_frequency,
    infer_frequency,
    periods_behind,
)


def _period_ends(count: int, step: int) -> list[dt.date]:
    """Month ends ``step`` months apart, the last one in December 2024."""
    dates = []
    for back in range(count - 1, -1, -1):
        index = 2024 * 12 + 11 - step * back
        year, month = divmod(index, 12)
        dates.append(dt.date(year, month + 1, calendar.monthrange(year, month + 1)[1]))
    return dates


def _weekdays(count: int) -> list[dt.date]:
    days: list[dt.date] = []
    day = dt.date(2024, 1, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        (_weekdays(60), Frequency("daily", 252)),
        (
            [dt.date(2024, 1, 5) + dt.timedelta(weeks=week) for week in range(30)],
            Frequency("weekly", 52),
        ),
        (_period_ends(24, 1), Frequency("monthly", 12)),
        (_period_ends(12, 3), Frequency("quarterly", 4)),
        (_period_ends(12, 12), Frequency("annual", 1)),
    ],
)
def test_the_frequency_is_inferred_from_the_dates(
    dates: list[dt.date], expected: Frequency
) -> None:
    assert infer_frequency(dates) == expected


def test_a_stated_count_that_agrees_is_kept_and_unnamed_counts_have_no_name() -> None:
    assert check_frequency(_period_ends(24, 1), 12) == Frequency("monthly", 12)
    assert check_frequency(_period_ends(24, 2), 6) == Frequency("", 6)


def test_a_stated_count_that_disagrees_names_the_likely_count() -> None:
    with pytest.raises(
        TableError, match=r"periods_per_year is 252.*median \d+ days apart"
    ) as found:
        check_frequency(_period_ends(24, 1), 252)
    assert "Set periods_per_year = 12 (monthly)" in str(found.value)


def test_dates_matching_no_frequency_are_refused() -> None:
    every_three_days = [dt.date(2024, 1, 1) + dt.timedelta(days=3 * n) for n in range(20)]
    with pytest.raises(TableError, match="matches no frequency"):
        infer_frequency(every_three_days)


def test_fewer_than_two_dates_cannot_be_inferred() -> None:
    with pytest.raises(TableError, match="fewer than two dates"):
        infer_frequency([dt.date(2024, 1, 1)])


def test_daily_staleness_counts_weekdays_so_a_weekend_is_not_behind() -> None:
    daily = Frequency("daily", 252)
    assert periods_behind(dt.date(2024, 11, 29), dt.date(2024, 12, 2), daily) == 1.0
    assert periods_behind(dt.date(2024, 11, 29), dt.date(2024, 12, 9), daily) == 6.0


def test_monthly_staleness_counts_average_periods() -> None:
    monthly = Frequency("monthly", 12)
    assert periods_behind(dt.date(2024, 1, 31), dt.date(2024, 3, 31), monthly) == pytest.approx(
        60 / (365.25 / 12), rel=1e-9
    )
    assert periods_behind(dt.date(2024, 3, 31), dt.date(2024, 1, 31), monthly) == 0.0
