"""Horizons by a trading calendar: NYSE's month end and session coverage, against weekdays.

As of 2024-03-28, NYSE's last March session (Good Friday closed the 29th), a 3M horizon starts at
the last session of December 2023, 2023-12-29, a Friday. Weekdays start a day earlier, 2023-12-28,
so a 3M return that includes 2023-12-29 differs from one that leaves it out.
"""

import datetime as dt

import numpy as np
import pytest

from quant_toolbox.data import SessionCalendar, as_calendar, set_default_calendar
from quant_toolbox.performance import analyze, horizon_return

NYSE_2023_HOLIDAYS = [
    "2023-01-02",
    "2023-01-16",
    "2023-02-20",
    "2023-04-07",
    "2023-05-29",
    "2023-06-19",
    "2023-07-04",
    "2023-09-04",
    "2023-11-23",
    "2023-12-25",
]
NYSE_2024_HOLIDAYS = [
    "2024-01-01",
    "2024-01-15",
    "2024-02-19",
    "2024-03-29",
    "2024-05-27",
    "2024-06-19",
    "2024-07-04",
    "2024-09-02",
    "2024-11-28",
    "2024-12-25",
]
AS_OF = dt.date(2024, 3, 28)
SPIKE = dt.date(2023, 12, 29)


def _nyse() -> SessionCalendar:
    return as_calendar(
        None,
        "2023-01-01",
        "2024-12-31",
        holidays=NYSE_2023_HOLIDAYS + NYSE_2024_HOLIDAYS,
        name="XNYS",
    )


def _series(calendar: SessionCalendar) -> tuple[list[dt.date], np.ndarray]:
    dates = calendar.sessions("2023-06-01", "2024-03-28")
    returns = np.full(len(dates), 0.001)
    returns[dates.index(SPIKE)] = 0.05
    return dates, returns


def _compounded(returns: np.ndarray, dates: list[dt.date], keep: set[dt.date]) -> float:
    picked = [value for day, value in zip(dates, returns, strict=True) if day in keep]
    return float(np.prod(1.0 + np.array(picked))) - 1.0


def test_nyse_3m_starts_at_the_last_december_session() -> None:
    nyse = _nyse()
    dates, returns = _series(nyse)
    assert nyse.period_ends(AS_OF, AS_OF, "M") == [AS_OF]

    expected = _compounded(returns, dates, {day for day in dates if day > SPIKE})
    assert horizon_return(dates, returns, "3M", 252, calendar=nyse) == pytest.approx(expected)
    # the spike is left out: the 3M return is about 0.1%, not about 5.1%
    assert expected == pytest.approx((1.001 ** (len(dates) - dates.index(SPIKE) - 1)) - 1.0)


def test_weekdays_3m_includes_the_spike() -> None:
    weekdays = as_calendar(None, "2023-01-01", "2024-12-31", name="weekdays")
    dates, returns = _series(weekdays)
    # no calendar: the start is 2023-12-28, so 2023-12-29 is included
    expected = _compounded(returns, dates, {day for day in dates if day > dt.date(2023, 12, 28)})
    assert horizon_return(dates, returns, "3M", 252) == pytest.approx(expected)
    assert expected > 0.05


def test_the_configured_default_gives_the_calendar_result() -> None:
    nyse = _nyse()
    dates, returns = _series(nyse)
    expected = horizon_return(dates, returns, "3M", 252, calendar=nyse)
    set_default_calendar(nyse)
    try:
        assert horizon_return(dates, returns, "3M", 252) == pytest.approx(expected)
    finally:
        set_default_calendar(None)


def test_dates_outside_the_calendars_range_do_not_raise() -> None:
    short = as_calendar(None, "2024-01-01", "2024-02-29", name="short")
    dates = [dt.date(2023, 6, 1) + dt.timedelta(days=index) for index in range(0, 300, 7)]
    returns = np.full(len(dates), 0.001)
    # as of 2023-12-27 is outside the calendar's days: nothing is raised, and the start falls back
    value = horizon_return(dates, returns, "3M", 52, calendar=short)
    assert isinstance(value, float)


def test_analyze_passes_the_calendar_and_records_its_name() -> None:
    nyse = _nyse()
    dates, returns = _series(nyse)
    result = analyze(dates, returns, 252, calendar=nyse, horizons=("3M",))
    assert result.conventions["calendar"] == "XNYS + 20 holidays"
    assert result.horizons["3M"] == pytest.approx(
        horizon_return(dates, returns, "3M", 252, calendar=nyse)
    )
    assert "calendar" not in analyze(dates, returns, 252).conventions
