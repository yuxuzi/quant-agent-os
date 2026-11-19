"""Trading calendars: known answers for NYSE's 2024, the caller's edits, and each kind of source.

NYSE's 2024 has 252 sessions: 262 weekdays less ten holidays (New Year's Day, Martin Luther King
Jr. Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day,
Thanksgiving, Christmas). The US federal calendar differs by Good Friday (NYSE closed) and by
Columbus Day and Veterans Day (NYSE open).

exchange_calendars and pandas_market_calendars are read by duck typing, so fakes with their methods
stand in for them; pandas, when installed, is used for real.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_toolbox.data import (
    Calendar,
    CalendarError,
    SessionCalendar,
    Table,
    TableError,
    align_to_calendar,
    as_calendar,
    configured_calendar,
    default_calendar,
    either,
    joint,
    session_gaps,
    set_default_calendar,
    using_calendar,
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


def test_weekdays_with_listed_holidays_give_nyse_2024() -> None:
    calendar = _nyse()
    assert calendar.count(FIRST, LAST) == len(calendar) == 252
    assert calendar.name == "XNYS + 10 holidays"
    assert not calendar.is_session("2024-03-29")
    assert calendar.is_session(dt.date(2024, 10, 14))
    assert calendar.sessions_per_year() == 252.0


def test_holidays_accept_a_numpy_array() -> None:
    holidays = np.array(NYSE_2024_HOLIDAYS, dtype="datetime64[D]")
    assert len(as_calendar(None, FIRST, LAST, holidays=holidays)) == 252


def test_weekdays_alone_say_they_have_no_holidays() -> None:
    calendar = as_calendar(None, FIRST, LAST)
    assert len(calendar) == 262
    assert calendar.name == "weekdays (no holidays)"


def test_a_weekmask_sets_the_weekend() -> None:
    sunday_to_thursday = as_calendar(None, "2024-06-02", "2024-06-08", weekmask="1111001")
    assert sunday_to_thursday.sessions("2024-06-02", "2024-06-08") == [
        dt.date(2024, 6, day) for day in (2, 3, 4, 5, 6)
    ]


def test_open_days_override_the_source_and_a_day_in_both_is_refused() -> None:
    calendar = as_calendar(_nyse(), open_days=["2024-03-29", "2024-03-30"])
    assert calendar.is_session("2024-03-29") and calendar.is_session("2024-03-30")
    assert calendar.name == "XNYS + 10 holidays + 2 open days"
    with pytest.raises(CalendarError, match="2024-07-05"):
        as_calendar(None, FIRST, LAST, holidays=["2024-07-05"], open_days=["2024-07-05"])


def test_days_outside_the_known_range_are_refused() -> None:
    calendar = _nyse()
    with pytest.raises(CalendarError, match="outside"):
        calendar.is_session("2025-01-02")
    with pytest.raises(CalendarError, match="outside"):
        calendar.sessions("2023-12-29", "2024-01-05")
    with pytest.raises(CalendarError, match="outside"):
        calendar.offset("2024-12-31", 1)
    with pytest.raises(CalendarError, match="first and last"):
        as_calendar(None, holidays=NYSE_2024_HOLIDAYS)


def test_offset_counts_sessions_from_a_session() -> None:
    calendar = _nyse()
    assert calendar.offset("2024-03-27", 2) == dt.date(2024, 4, 1)  # over Good Friday
    assert calendar.offset("2024-04-01", -1) == dt.date(2024, 3, 28)
    with pytest.raises(CalendarError, match="roll it first"):
        calendar.offset("2024-03-29", 1)


def test_roll_conventions() -> None:
    calendar = _nyse()
    assert calendar.roll("2024-03-28") == dt.date(2024, 3, 28)
    assert calendar.roll("2024-03-29", "following") == dt.date(2024, 4, 1)
    assert calendar.roll("2024-03-29", "modified_following") == dt.date(2024, 3, 28)
    assert calendar.roll("2024-03-29", "preceding") == dt.date(2024, 3, 28)
    assert calendar.roll("2024-06-01", "modified_preceding") == dt.date(2024, 6, 3)


def test_period_ends_are_last_sessions_the_calendar_sees_in_full() -> None:
    calendar = _nyse()
    months = calendar.period_ends(FIRST, LAST, "M")
    assert len(months) == 12
    assert months[2] == dt.date(2024, 3, 28)  # Good Friday is the 29th
    assert calendar.period_ends(FIRST, LAST, "Q") == [
        dt.date(2024, 3, 28),
        dt.date(2024, 6, 28),
        dt.date(2024, 9, 30),
        dt.date(2024, 12, 31),
    ]
    assert calendar.period_ends(FIRST, "2024-03-28", "M")[-1] == dt.date(2024, 3, 28)
    assert calendar.period_ends(FIRST, "2024-03-27", "M")[-1] == dt.date(2024, 2, 29)
    assert calendar.period_ends("2024-03-25", "2024-04-07", "W") == [
        dt.date(2024, 3, 28),
        dt.date(2024, 4, 5),
    ]
    partial = as_calendar(None, FIRST, "2024-03-20")
    assert partial.period_ends(FIRST, "2024-03-20", "M")[-1] == dt.date(2024, 2, 29)


def test_mask_marks_each_day() -> None:
    mask = _nyse().mask(["2024-03-28", "2024-03-29", "2024-03-30", "2024-04-01"])
    assert mask.tolist() == [True, False, False, True]


def test_from_sessions_knows_the_days_between_its_first_and_last() -> None:
    calendar = SessionCalendar.from_sessions(
        ["2024-01-02", "2024-01-03", "2024-01-05"], name="vendor"
    )
    assert (calendar.first, calendar.last) == (dt.date(2024, 1, 2), dt.date(2024, 1, 5))
    assert not calendar.is_session("2024-01-04")
    assert isinstance(calendar, Calendar)


def test_joint_and_either_combine_over_the_shared_range() -> None:
    london = as_calendar(None, "2024-03-25", "2024-04-30", holidays=["2024-03-29", "2024-04-01"])
    new_york = _nyse()
    both = joint(london, new_york)
    assert (both.first, both.last) == (dt.date(2024, 3, 25), dt.date(2024, 4, 30))
    assert not both.is_session("2024-04-01")
    assert either(london, new_york).is_session("2024-04-01")
    assert both.name == "weekdays + 2 holidays & XNYS + 10 holidays"


class _FirmCalendar:
    """A firm's own calendar class, with plain attributes."""

    name = "firm"
    first = dt.date(2024, 1, 1)
    last = dt.date(2024, 1, 31)

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        return [day for day in _nyse().sessions(start, end)]


def test_a_calendar_of_the_firms_own_is_read_and_extended() -> None:
    calendar = as_calendar(_FirmCalendar(), holidays=["2024-01-09"])  # a day of mourning
    assert calendar.count(calendar.first, calendar.last) == 20
    assert calendar.name == "firm + 1 holiday"


class _FakeExchangeCalendar:
    """The part of an exchange_calendars calendar that is read."""

    name = "XNYS"
    first_session = dt.datetime(2024, 1, 2)
    last_session = dt.datetime(2024, 12, 31)

    def sessions_in_range(self, start: str, end: str) -> np.ndarray:
        return np.array(_nyse().sessions(start, end), dtype="datetime64[ns]")


class _FakeMarketCalendar:
    """The part of a pandas_market_calendars calendar that is read."""

    name = "NYSE"

    def valid_days(self, start_date: str, end_date: str) -> np.ndarray:
        return np.array(_nyse().sessions(start_date, end_date), dtype="datetime64[ns]")


def test_an_exchange_calendars_calendar_brings_its_own_range() -> None:
    calendar = as_calendar(_FakeExchangeCalendar())
    assert (calendar.first, calendar.last) == (dt.date(2024, 1, 2), LAST)
    assert len(calendar) == 252
    assert calendar.name == "XNYS"


def test_a_pandas_market_calendars_calendar_needs_a_range() -> None:
    with pytest.raises(CalendarError, match="first and last"):
        as_calendar(_FakeMarketCalendar())
    calendar = as_calendar(_FakeMarketCalendar(), FIRST, LAST, holidays=["2024-01-09"])
    assert len(calendar) == 251


def test_a_numpy_business_day_calendar_is_read() -> None:
    source = np.busdaycalendar(holidays=NYSE_2024_HOLIDAYS)
    assert len(as_calendar(source, FIRST, LAST)) == 252


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(CalendarError, match="cannot read a calendar from int"):
        as_calendar(3, FIRST, LAST)


def test_pandas_federal_rules_with_the_callers_edits_give_nyse_2024() -> None:
    pandas = pytest.importorskip("pandas")
    from pandas.tseries.holiday import USFederalHolidayCalendar

    federal = as_calendar(USFederalHolidayCalendar(), FIRST, LAST)
    assert len(federal) == 251  # closed on Columbus and Veterans Day, open on Good Friday
    nyse = as_calendar(
        USFederalHolidayCalendar(),
        FIRST,
        LAST,
        holidays=["2024-03-29"],
        open_days=["2024-10-14", "2024-11-11"],
    )
    assert nyse.sessions(FIRST, LAST) == _nyse().sessions(FIRST, LAST)
    offset = pandas.offsets.CustomBusinessDay(holidays=NYSE_2024_HOLIDAYS)
    assert len(as_calendar(offset, FIRST, LAST)) == 252
    aware = pandas.DatetimeIndex(_nyse().sessions(FIRST, LAST)).tz_localize("UTC")
    assert len(SessionCalendar.from_sessions(aware, name="UTC index")) == 252


def _prices() -> Table:
    days = [dt.date(2024, 3, day) for day in (26, 27, 29, 30)] + [dt.date(2024, 4, 2)]
    return Table(days, ["A"], np.array([[1.0], [2.0], [3.0], [4.0], [5.0]]))


def test_with_no_default_set_the_default_is_named_weekdays() -> None:
    assert default_calendar().name == "weekdays (no holidays)"
    assert default_calendar().is_session("2024-03-29")
    assert configured_calendar() is None


def test_a_default_calendar_applies_where_none_is_given_and_a_block_can_use_another() -> None:
    nyse = _nyse()
    weekdays = default_calendar()
    try:
        assert set_default_calendar(nyse) is nyse
        assert configured_calendar() is nyse
        assert session_gaps(_prices()) == (
            [dt.date(2024, 3, 28), dt.date(2024, 4, 1)],
            [dt.date(2024, 3, 29), dt.date(2024, 3, 30)],
        )
        with using_calendar(as_calendar(None, FIRST, LAST)) as scoped:
            assert default_calendar() is scoped
            assert session_gaps(_prices())[1] == [dt.date(2024, 3, 30)]
        assert default_calendar() is nyse
    finally:
        set_default_calendar(None)
    assert default_calendar().name == weekdays.name


def test_align_to_calendar_puts_one_row_on_each_session() -> None:
    with pytest.raises(TableError, match="2 row\\(s\\) dated on a day XNYS"):
        align_to_calendar(_prices(), _nyse())
    aligned = align_to_calendar(_prices(), _nyse(), off_session="drop")
    assert aligned.dates == [
        dt.date(2024, 3, 26),
        dt.date(2024, 3, 27),
        dt.date(2024, 3, 28),
        dt.date(2024, 4, 1),
        dt.date(2024, 4, 2),
    ]
    assert np.isnan(aligned.values[2, 0]) and np.isnan(aligned.values[3, 0])
    filled = align_to_calendar(_prices(), _nyse(), fill="previous", off_session="drop")
    assert filled.values[:, 0].tolist() == [1.0, 2.0, 2.0, 2.0, 5.0]


class _HalfDays(SessionCalendar):
    """A firm's subclass, overriding one method for its own use."""

    def is_session(self, day: dt.date | np.datetime64 | str) -> bool:
        return super().is_session(day) and str(day) != "2024-11-29"


def test_a_subclass_is_kept_by_as_calendar_and_as_the_default() -> None:
    nyse = _nyse()
    days = np.array(nyse.sessions(FIRST, LAST), dtype="datetime64[D]")
    firm = _HalfDays(days, nyse.first, nyse.last, "firm")
    assert as_calendar(firm) is firm
    with using_calendar(firm):
        assert not default_calendar().is_session("2024-11-29")
    assert type(as_calendar(firm, holidays=["2024-11-29"])) is SessionCalendar


def test_sessions_from_counts_forward_from_a_day() -> None:
    assert _nyse().sessions_from("2024-03-28", 3) == [
        dt.date(2024, 3, 28),
        dt.date(2024, 4, 1),
        dt.date(2024, 4, 2),
    ]
    with pytest.raises(CalendarError, match="run past"):
        _nyse().sessions_from("2024-12-30", 3)
