"""Trading calendars: the days a market, or a settlement system, is open.

A calendar here is its sessions, held as a sorted array of days between a first and a last day it
knows. Outside those days it knows nothing, and every method says so with a `CalendarError` rather
than guessing that a weekday is a session: a holiday list is finite, and a market closes for
storms, funerals, and outages no rule predicts.

`as_calendar` builds one from what the caller already has, read by duck typing, so this module
imports none of these libraries:

- an `exchange_calendars` calendar (``sessions_in_range``, bounded by its first and last session);
- a `pandas_market_calendars` calendar (``valid_days``);
- a pandas ``CustomBusinessDay``, a pandas ``AbstractHolidayCalendar`` subclass such as
  ``USFederalHolidayCalendar``, or a ``numpy.busdaycalendar``;
- any object with ``name``, ``first``, ``last``, and ``sessions(start, end)`` (a `Calendar`), such
  as a firm's own holiday database;
- nothing: weekdays, with only the holidays the caller lists.

Holidays the caller lists are added to the source's own (a union), and days listed as open override
both. A rule-based calendar is often near but not equal to a market: ``USFederalHolidayCalendar``
closes on Columbus Day and Veterans Day, when NYSE is open, and opens on Good Friday, when NYSE is
closed. ``holidays=["2024-03-29"], open_days=["2024-10-14", "2024-11-11"]`` makes it NYSE's 2024.
A day listed as both is an error. The calendar's name records each edit, so a report can say which
calendar it used.

Sessions are days; early closes and intraday hours are out of scope, and so are day counts.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal, Protocol, cast, runtime_checkable

import numpy as np

from quant_toolbox._types import BoolArray
from quant_toolbox.data._tables import Table, TableError

DayLike = dt.date | np.datetime64 | str
Roll = Literal["following", "preceding", "modified_following", "modified_preceding"]
Frequency = Literal["W", "M", "Q", "Y"]

_WEEKDAYS = "1111100"
_WEEKDAYS_FIRST, _WEEKDAYS_LAST = dt.date(1970, 1, 1), dt.date(2099, 12, 31)
_ONE_DAY = np.timedelta64(1, "D")
_DEFAULT: SessionCalendar | None = None  # set by the fund
_FALLBACK: SessionCalendar | None = None  # weekdays, built on first use
_SCOPED: ContextVar[SessionCalendar | None] = ContextVar("quant_toolbox_calendar", default=None)


class CalendarError(ValueError):
    """A day outside a calendar's known range, a day that is not a session, or a bad source."""


@runtime_checkable
class Calendar(Protocol):
    """What the toolbox needs of a calendar: its name, its known range, and its sessions."""

    @property
    def name(self) -> str:
        """Say which calendar this is, for a report: ``"XNYS"``, ``"weekdays (no holidays)"``."""
        ...

    @property
    def first(self) -> dt.date:
        """Return the first day the calendar knows."""
        ...

    @property
    def last(self) -> dt.date:
        """Return the last day the calendar knows."""
        ...

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Return the sessions from ``start`` to ``end``, both included."""
        ...


class SessionCalendar:
    """A calendar as its sessions: a sorted array of days, known from ``first`` to ``last``.

    Build one with `as_calendar`, or with `SessionCalendar.from_sessions` from a list of the days a
    market traded (a vendor's file, or a price file's own dates).
    """

    __slots__ = ("_days", "_first", "_last", "_name")

    def __init__(self, days: np.ndarray, first: dt.date, last: dt.date, name: str) -> None:
        if first > last:
            raise CalendarError(f"{name}: first day {first} is after last day {last}")
        days = np.unique(np.asarray(days, dtype="datetime64[D]"))
        low, high = np.datetime64(first, "D"), np.datetime64(last, "D")
        self._days = days[(days >= low) & (days <= high)]
        self._first, self._last, self._name = first, last, name

    @classmethod
    def from_sessions(
        cls,
        days: Iterable[DayLike],
        *,
        name: str,
        first: DayLike | None = None,
        last: DayLike | None = None,
    ) -> SessionCalendar:
        """Return the calendar whose sessions are ``days``, known from the first to the last."""
        array = _days(days)
        if array.size == 0 and (first is None or last is None):
            raise CalendarError(f"{name}: no sessions, and no first and last day to bound them")
        known_first = _day(first) if first is not None else _date(array.min())
        known_last = _day(last) if last is not None else _date(array.max())
        return cls(array, known_first, known_last, name)

    @property
    def name(self) -> str:
        """Say which calendar this is, with any holidays or open days the caller added."""
        return self._name

    @property
    def first(self) -> dt.date:
        """Return the first day the calendar knows."""
        return self._first

    @property
    def last(self) -> dt.date:
        """Return the last day the calendar knows."""
        return self._last

    def __repr__(self) -> str:
        return f"SessionCalendar({self._name!r}, {self._first} to {self._last})"

    def __len__(self) -> int:
        return int(self._days.size)

    def sessions(self, start: DayLike, end: DayLike) -> list[dt.date]:
        """Return the sessions from ``start`` to ``end``, both included."""
        low, high = self._range(start, end)
        return [_date(day) for day in self._days[low:high]]

    def sessions_from(self, start: DayLike, count: int) -> list[dt.date]:
        """Return the first ``count`` sessions on or after ``start``."""
        low = int(np.searchsorted(self._days, self._known(start), side="left"))
        if count < 0 or low + count > self._days.size:
            raise CalendarError(
                f"{self._name}: {count} sessions from {_day(start)} run past {self._last}"
            )
        return [_date(day) for day in self._days[low : low + count]]

    def count(self, start: DayLike, end: DayLike) -> int:
        """Return how many sessions fall from ``start`` to ``end``, both included."""
        low, high = self._range(start, end)
        return max(high - low, 0)

    def is_session(self, day: DayLike) -> bool:
        """Return whether ``day`` is a session."""
        target = self._known(day)
        index = int(np.searchsorted(self._days, target))
        return index < self._days.size and self._days[index] == target

    def mask(self, days: Iterable[DayLike]) -> BoolArray:
        """Return, for each of ``days``, whether it is a session."""
        targets = _days(days, unique=False)
        for target in (targets.min(), targets.max()) if targets.size else ():
            self._known(target)
        index = np.searchsorted(self._days, targets)
        found = np.zeros(targets.shape, dtype=bool)
        inside = index < self._days.size
        found[inside] = self._days[index[inside]] == targets[inside]
        return found

    def offset(self, day: DayLike, sessions: int) -> dt.date:
        """Return the session ``sessions`` after ``day`` (before it, when negative).

        ``day`` must be a session; `roll` one that may not be first, by the convention that applies.
        """
        target = self._known(day)
        index = int(np.searchsorted(self._days, target))
        if index >= self._days.size or self._days[index] != target:
            raise CalendarError(f"{self._name}: {_date(target)} is not a session; roll it first")
        moved = index + sessions
        if not 0 <= moved < self._days.size:
            raise CalendarError(
                f"{self._name}: {sessions} sessions from {_date(target)} is outside "
                f"{self._first} to {self._last}"
            )
        return _date(self._days[moved])

    def roll(self, day: DayLike, convention: Roll = "following") -> dt.date:
        """Return ``day`` if it is a session, else the session a business-day convention picks.

        ``following`` takes the next session and ``preceding`` the previous one. The ``modified``
        conventions stay in ``day``'s month: when the first choice is in another month, they take
        the other direction (ISDA 2006, section 4.12).
        """
        target = self._known(day)
        index = int(np.searchsorted(self._days, target))
        if index < self._days.size and self._days[index] == target:
            return _date(target)
        after = _date(self._days[index]) if index < self._days.size else None
        before = _date(self._days[index - 1]) if index > 0 else None
        forward = convention in ("following", "modified_following")
        chosen, other = (after, before) if forward else (before, after)
        month = _date(target).month
        if convention.startswith("modified") and (chosen is None or chosen.month != month):
            chosen = other
        if chosen is None:
            raise CalendarError(
                f"{self._name}: no session to roll {_date(target)} to by {convention} within "
                f"{self._first} to {self._last}"
            )
        return chosen

    def period_ends(
        self, start: DayLike, end: DayLike, frequency: Frequency = "M"
    ) -> list[dt.date]:
        """Return the last session of each week, month, quarter, or year from ``start`` to ``end``.

        A period counts only when the calendar knows it to its last day, so the last session
        before ``last`` is not taken for a month end the calendar cannot see.
        """
        low, high = self._range(start, end)
        keys = _period_keys(self._days, frequency)
        beyond = _period_keys(np.array([np.datetime64(self._last, "D") + _ONE_DAY]), frequency)
        ends = np.flatnonzero(keys != np.append(keys[1:], beyond))
        return [_date(self._days[index]) for index in ends[(ends >= low) & (ends < high)]]

    def sessions_per_year(self) -> float:
        """Return the mean number of sessions in the calendar years the calendar knows in full.

        It is read beside a stated ``periods_per_year``, such as 252; it does not replace it.
        """
        first_year = self._first.year + (self._first != dt.date(self._first.year, 1, 1))
        last_year = self._last.year - (self._last != dt.date(self._last.year, 12, 31))
        if first_year > last_year:
            raise CalendarError(f"{self._name}: knows no full calendar year")
        start, end = dt.date(first_year, 1, 1), dt.date(last_year, 12, 31)
        return self.count(start, end) / (last_year - first_year + 1)

    def _known(self, day: DayLike) -> np.datetime64:
        target = np.datetime64(_day(day), "D")
        if not np.datetime64(self._first, "D") <= target <= np.datetime64(self._last, "D"):
            raise CalendarError(
                f"{self._name}: {_date(target)} is outside the days it knows, "
                f"{self._first} to {self._last}"
            )
        return target

    def _range(self, start: DayLike, end: DayLike) -> tuple[int, int]:
        low = int(np.searchsorted(self._days, self._known(start), side="left"))
        high = int(np.searchsorted(self._days, self._known(end), side="right"))
        return low, high


def as_calendar(
    source: object = None,
    first: DayLike | None = None,
    last: DayLike | None = None,
    *,
    holidays: Iterable[DayLike] = (),
    open_days: Iterable[DayLike] = (),
    weekmask: str | None = None,
    name: str | None = None,
) -> SessionCalendar:
    """Return ``source`` as a `SessionCalendar` from ``first`` to ``last``, with the caller's edits.

    ``source`` is read by duck typing (see the module's docstring); None is weekdays. ``first`` and
    ``last`` default to the source's own range when it has one (a `Calendar`, an
    `exchange_calendars` calendar) and are required otherwise.

    ``holidays`` are closures added to the source's own: ad hoc ones (a day of mourning, a storm)
    or ones a rule-based source lacks. ``open_days`` override the source: each is a session even
    when the source, or its weekmask, closes it. A day in both is an error. ``weekmask`` (seven
    ``0``/``1`` characters from Monday, ``"1111100"`` by default) applies only to a source given as
    rules, not to one given as sessions. ``name`` replaces the source's name; the edits are
    appended to it either way.

    A `SessionCalendar`, or a firm's subclass of one, given with nothing else is returned as is, so
    a subclass's own methods stay in use. Edits or a range build a plain `SessionCalendar`.
    """
    added, opened = _days(holidays), _days(open_days)
    unchanged = (first, last, weekmask, name) == (None, None, None, None)
    if isinstance(source, SessionCalendar) and unchanged and not added.size and not opened.size:
        return source
    both = np.intersect1d(added, opened)
    if both.size:
        listed = ", ".join(str(_date(day)) for day in both)
        raise CalendarError(f"listed both as a holiday and as an open day: {listed}")
    base, known_first, known_last, base_name = _read(source, first, last, weekmask)
    days = np.setdiff1d(base, added)
    days = np.union1d(days, opened)
    if source is None and added.size:
        base_name = "weekdays"
    label = name or base_name
    if added.size:
        label += f" + {added.size} holiday{'s' * (added.size != 1)}"
    if opened.size:
        label += f" + {opened.size} open day{'s' * (opened.size != 1)}"
    return SessionCalendar(days, known_first, known_last, label)


def configured_calendar() -> SessionCalendar | None:
    """Return the calendar the fund chose, by `using_calendar` or `set_default_calendar`, or None.

    A check that would be noise against plain weekdays (a holiday reported as a missing session)
    runs only when this is not None, or when given a calendar.
    """
    scoped = _SCOPED.get()
    return scoped if scoped is not None else _DEFAULT


def default_calendar() -> SessionCalendar:
    """Return the calendar a function uses when given none.

    That is the one `using_calendar` set for the current block, else the one `set_default_calendar`
    set for the process, else weekdays from 1970 to 2099, named ``"weekdays (no holidays)"``.
    """
    chosen = configured_calendar()
    if chosen is not None:
        return chosen
    global _FALLBACK
    if _FALLBACK is None:
        _FALLBACK = as_calendar(None, _WEEKDAYS_FIRST, _WEEKDAYS_LAST)
    return _FALLBACK


def set_default_calendar(calendar: object) -> SessionCalendar:
    """Set the calendar every function uses when given none, for the whole process, and return it.

    Call it once at start-up with the fund's main calendar. ``calendar`` is anything `as_calendar`
    reads with no range (so a rule-based source goes through `as_calendar` first); None restores
    weekdays.
    """
    global _DEFAULT
    _DEFAULT = None if calendar is None else as_calendar(calendar)
    return default_calendar()


@contextmanager
def using_calendar(calendar: object) -> Iterator[SessionCalendar]:
    """Use ``calendar`` as the default inside a ``with`` block, in this thread or task only.

    For a fund on several exchanges, ``with using_calendar(xlon): ...`` runs one market on its
    own calendar, and the process default returns after.
    """
    chosen = as_calendar(calendar)
    token = _SCOPED.set(chosen)
    try:
        yield chosen
    finally:
        _SCOPED.reset(token)


def align_to_calendar(
    table: Table,
    calendar: object = None,
    *,
    fill: Literal["none", "previous"] = "none",
    off_session: Literal["raise", "drop"] = "raise",
) -> Table:
    """Return ``table`` with one row per session, from its first date to its last.

    ``calendar`` is anything `as_calendar` reads with no range, or None for `default_calendar`. A
    session the table lacks is NaN, or, with ``fill="previous"``, the previous row's value (for
    prices and positions; never for returns, whose missing day is not a zero). A row dated on a day
    that is not a session is an error naming it, or is left out with ``off_session="drop"``: a
    weekend print, or a holiday row a vendor filled.
    """
    chosen = _resolve(calendar)
    _, off = session_gaps(table, chosen)
    if off and off_session == "raise":
        listed = ", ".join(map(str, off[:5])) + (" ..." if len(off) > 5 else "")
        raise TableError(
            f"{len(off)} row(s) dated on a day {chosen.name} has no session: {listed}. "
            "Drop them with off_session='drop', or use a calendar that opens those days."
        )
    dropped = set(off)
    kept = [day for day in table.dates if day not in dropped]
    if not kept:
        raise TableError(f"No row of the table is dated on a session of {chosen.name}.")
    sessions = chosen.sessions(kept[0], kept[-1])
    rows = {day: index for index, day in enumerate(table.dates)}
    values = np.full((len(sessions), len(table.columns)), np.nan)
    for row, day in enumerate(sessions):
        if day in rows:
            values[row] = table.values[rows[day]]
        elif fill == "previous" and row:
            values[row] = values[row - 1]
    return Table(sessions, list(table.columns), values)


def session_gaps(table: Table, calendar: object = None) -> tuple[list[dt.date], list[dt.date]]:
    """Return the sessions ``table`` lacks, and its dates that are not sessions, in its own range.

    ``calendar`` is anything `as_calendar` reads with no range, or None for `default_calendar`.
    """
    chosen = _resolve(calendar)
    sessions = chosen.sessions(table.dates[0], table.dates[-1])
    present = set(table.dates)
    missing = [day for day in sessions if day not in present]
    on_session = chosen.mask(table.dates)
    off = [day for day, open_ in zip(table.dates, on_session.tolist(), strict=True) if not open_]
    return missing, off


def _resolve(calendar: object) -> SessionCalendar:
    return default_calendar() if calendar is None else as_calendar(calendar)


def joint(*calendars: object) -> SessionCalendar:
    """Return the sessions every calendar shares: a cross-market trade, or an FX value date.

    Each calendar goes through `as_calendar` with no range, so each must carry its own; the result
    knows only the days they all know.
    """
    return _combine(calendars, np.intersect1d, " & ")


def either(*calendars: object) -> SessionCalendar:
    """Return the sessions any of the calendars has, over the days they all know."""
    return _combine(calendars, np.union1d, " | ")


def _combine(calendars: tuple[object, ...], merge: Any, joiner: str) -> SessionCalendar:
    if not calendars:
        raise CalendarError("no calendars to combine")
    read = [as_calendar(calendar) for calendar in calendars]
    first, last = max(each.first for each in read), min(each.last for each in read)
    if first > last:
        raise CalendarError(f"the calendars share no known days: {', '.join(map(repr, read))}")
    days = read[0]._days
    for each in read[1:]:
        days = merge(days, each._days)
    return SessionCalendar(days, first, last, joiner.join(each.name for each in read))


def _read(
    source: object, first: DayLike | None, last: DayLike | None, weekmask: str | None
) -> tuple[np.ndarray, dt.date, dt.date, str]:
    """Return a source's sessions, range, and name, by duck typing."""
    duck: Any = source
    if hasattr(duck, "sessions_in_range"):  # exchange_calendars
        known_first = _day(first) if first is not None else _day(duck.first_session)
        known_last = _day(last) if last is not None else _day(duck.last_session)
        days = _days(duck.sessions_in_range(known_first.isoformat(), known_last.isoformat()))
        return days, known_first, known_last, str(getattr(duck, "name", "exchange calendar"))
    if hasattr(duck, "valid_days"):  # pandas_market_calendars
        known_first, known_last = _bounds(duck, first, last)
        days = _days(duck.valid_days(known_first.isoformat(), known_last.isoformat()))
        return days, known_first, known_last, str(getattr(duck, "name", "market calendar"))
    if isinstance(source, Calendar):
        known_first = _day(first) if first is not None else source.first
        known_last = _day(last) if last is not None else source.last
        days = _days(source.sessions(known_first, known_last))
        return days, known_first, known_last, source.name
    known_first, known_last = _bounds(source, first, last)
    holidays, mask, label = _rules(source, known_first, known_last)
    calendar = np.busdaycalendar(weekmask=weekmask or mask, holidays=holidays)
    every = np.arange(
        np.datetime64(known_first, "D"),
        np.datetime64(known_last, "D") + _ONE_DAY,
        dtype="datetime64[D]",
    )
    return every[np.is_busday(every, busdaycal=calendar)], known_first, known_last, label


def _rules(source: object, first: dt.date, last: dt.date) -> tuple[np.ndarray, str, str]:
    """Return a rule-based source's holidays, weekmask, and name."""
    if source is None:
        return _days(()), _WEEKDAYS, "weekdays (no holidays)"
    calendar = getattr(source, "calendar", None)  # a pandas CustomBusinessDay wraps a numpy one
    if isinstance(calendar, np.busdaycalendar):
        source = calendar
    if isinstance(source, np.busdaycalendar):
        mask = "".join("1" if open_ else "0" for open_ in source.weekmask)
        return _days(source.holidays), mask, "business days"
    duck: Any = source
    if callable(getattr(duck, "holidays", None)) and hasattr(duck, "rules"):  # pandas rules
        listed = duck.holidays(start=first.isoformat(), end=last.isoformat())
        return _days(listed), _WEEKDAYS, str(getattr(duck, "name", None) or type(duck).__name__)
    raise CalendarError(
        f"cannot read a calendar from {type(source).__name__}: pass an exchange_calendars or "
        "pandas_market_calendars calendar, a pandas CustomBusinessDay or holiday calendar, a "
        "numpy.busdaycalendar, a Calendar, or None with a list of holidays"
    )


def _bounds(source: object, first: DayLike | None, last: DayLike | None) -> tuple[dt.date, dt.date]:
    if first is None or last is None:
        kind = "weekdays" if source is None else type(source).__name__
        raise CalendarError(
            f"a calendar from {kind} is rules with no end: pass the first and last day to know"
        )
    return _day(first), _day(last)


def _period_keys(days: np.ndarray, frequency: Frequency) -> np.ndarray:
    if frequency == "W":  # weeks from Monday: 1970-01-01 was a Thursday
        return (days.astype("int64") + 3) // 7
    months = days.astype("datetime64[M]").astype("int64")
    if frequency == "M":
        return months
    if frequency == "Q":
        return months // 3
    if frequency == "Y":
        return months // 12
    raise CalendarError(f"unknown frequency {frequency!r}: use W, M, Q, or Y")


def _days(values: Iterable[Any], *, unique: bool = True) -> np.ndarray:
    """Return dates, ISO strings, numpy datetimes, or pandas timestamps as datetime64[D] days.

    With ``unique``, they are also sorted and each is kept once.
    """
    values = getattr(values, "values", values)  # a pandas index: its datetime64 values, in UTC
    if isinstance(values, np.ndarray) and np.issubdtype(values.dtype, np.datetime64):
        days = values.astype("datetime64[D]")
    else:
        days = np.array([_day(value) for value in values], dtype="datetime64[D]")
    return np.unique(days) if unique else days


def _day(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return _date(np.datetime64(value, "D"))
    except ValueError as error:
        raise CalendarError(f"not a day: {value!r}") from error


def _date(day: np.datetime64) -> dt.date:
    return cast(dt.date, day.astype("datetime64[D]").astype(dt.date))


def business_days(count: int, start: dt.date = dt.date(2021, 1, 4)) -> list[dt.date]:
    """Return ``count`` weekdays from ``start``, holidays ignored: for synthetic data only. A
    market's days are a `SessionCalendar`'s `sessions_from`."""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days
