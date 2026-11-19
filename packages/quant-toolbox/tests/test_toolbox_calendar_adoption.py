"""Calendars in use: synthetic data takes one, and return_notes checks sessions when one applies.

With no calendar set, synthetic data keeps its weekday dates, and return_notes says nothing new.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from quant_toolbox.data import (
    SessionCalendar,
    Table,
    as_calendar,
    business_days,
    demo_book,
    demo_panel,
    demo_returns,
    return_notes,
    set_default_calendar,
)

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
FIRST, LAST = dt.date(2024, 1, 1), dt.date(2024, 12, 31)


def _nyse() -> SessionCalendar:
    return as_calendar(None, FIRST, LAST, holidays=NYSE_2024_HOLIDAYS, name="XNYS")


def _table(dates: list[dt.date]) -> Table:
    values = np.full((len(dates), 2), 0.01)
    return Table(dates, ["A", "B"], values)


def test_no_calendar_keeps_the_weekday_dates() -> None:
    assert demo_returns().dates == business_days(1000)
    assert demo_panel().dates == business_days(750, dt.date(2020, 1, 1))
    assert demo_book()[0].dates == business_days(400, dt.date(2022, 1, 3))


def test_a_calendar_sets_the_synthetic_dates_and_drops_its_holidays() -> None:
    calendar = as_calendar(None, "2021-01-01", "2026-12-31", holidays=["2021-01-18"])
    returns = demo_returns(calendar=calendar)
    assert len(returns.dates) == 1000
    assert dt.date(2021, 1, 18) not in returns.dates
    assert all(calendar.is_session(day) for day in returns.dates)
    assert dt.date(2021, 1, 18) not in demo_book(calendar=calendar)[0].dates
    # demo_panel starts in 2020, so its calendar must know that year
    earlier = as_calendar(None, "2020-01-01", "2026-12-31", holidays=["2021-01-18"])
    assert dt.date(2021, 1, 18) not in demo_panel(calendar=earlier).dates


def test_return_notes_is_unchanged_with_no_calendar() -> None:
    dates = [dt.date(2024, 3, 27), dt.date(2024, 3, 29), dt.date(2024, 4, 1)]
    assert return_notes(_table(dates)) == []


def test_return_notes_with_a_calendar_notes_rows_off_sessions_and_missing_sessions() -> None:
    dates = [dt.date(2024, 3, 27), dt.date(2024, 3, 29), dt.date(2024, 4, 1)]
    notes = return_notes(_table(dates), calendar=_nyse())
    assert notes == [
        "1 row(s) dated on a day XNYS + 10 holidays has no session, the first 2024-03-29.",
        "1 session(s) of XNYS + 10 holidays have no row, the first 2024-03-28.",
    ]


def test_return_notes_uses_the_configured_calendar_when_none_is_given() -> None:
    dates = [dt.date(2024, 3, 27), dt.date(2024, 3, 29), dt.date(2024, 4, 1)]
    try:
        set_default_calendar(_nyse())
        notes = return_notes(_table(dates))
    finally:
        set_default_calendar(None)
    assert notes == [
        "1 row(s) dated on a day XNYS + 10 holidays has no session, the first 2024-03-29.",
        "1 session(s) of XNYS + 10 holidays have no row, the first 2024-03-28.",
    ]
    assert return_notes(_table(dates)) == []


def test_return_notes_says_sessions_were_not_checked_outside_the_calendar() -> None:
    dates = [dt.date(2023, 12, 29), dt.date(2024, 1, 2)]
    assert return_notes(_table(dates), calendar=_nyse()) == [
        "The dates run outside the days XNYS + 10 holidays knows (2024-01-01 to 2024-12-31); "
        "sessions were not checked.",
    ]
