# 0033: A trading calendar is its sessions, read from what the firm already uses

- Status: accepted; builds on [0026](0026-quant-toolbox.md) and [0027](0027-quant-toolbox-package.md)
- Date: 2026-11-19

## Context

Quant Toolbox's only calendar was `business_days`: weekdays, with holidays ignored. Its name promised more.
Elsewhere the toolbox inferred a calendar instead of asking for one: a month end is the last calendar
day, so a 3M horizon as of 2024-03-28 (NYSE's last March session; Good Friday closed it on the 29th) starts
a day early, and a horizon's coverage allows a guessed `366 / periods_per_year` days. The mock funds'
warehouse builders and nine generated-app test templates repeat the weekday loop.

A finance calendar is harder than a weekday rule. Exchanges close for unscheduled events (11 September 2001,
Hurricane Sandy, days of mourning). An exchange calendar is not a settlement calendar (SIFMA, TARGET2), and
a cross-market trade needs both. Libraries already keep these: `exchange_calendars`,
`pandas_market_calendars`, pandas' holiday rules, and firms' own holiday databases.

## Decision

- **One concrete type, `SessionCalendar`:** a sorted `datetime64[D]` array of sessions, known from `first`
  to `last`. Every operation (`sessions`, `count`, `is_session`, `mask`, `offset`, `roll`, `period_ends`,
  `sessions_per_year`) is a binary search in numpy, and a day outside the known range raises
  `CalendarError`. A finite holiday list never extends itself by guessing.
- **One protocol, `Calendar`:** `name`, `first`, `last`, and `sessions(start, end)`. A firm's own calendar
  class satisfies it with no adapter.
- **One way in, `as_calendar`, by duck typing:** an `exchange_calendars` calendar (`sessions_in_range`,
  bounded by its own sessions), a `pandas_market_calendars` calendar (`valid_days`), a pandas
  `CustomBusinessDay` (it wraps a `numpy.busdaycalendar`) or `AbstractHolidayCalendar`, a
  `numpy.busdaycalendar`, a `Calendar`, or nothing (weekdays). The toolbox imports none of these libraries,
  so it keeps numpy as its one requirement and adds no extra.
- **The caller's edits are explicit.** `holidays=` are added to the source's own (a union): ad hoc closures,
  or ones a rule-based source lacks. `open_days=` override the source. A day in both is an error. Pandas'
  `USFederalHolidayCalendar` is the common trap: it closes on Columbus and Veterans Day, when NYSE trades,
  and trades on Good Friday; one `holidays` and two `open_days` make it NYSE's 2024, and a test holds
  that to 252 sessions.
- **The name is evidence.** A calendar's name records the edits (`"XNYS + 1 holiday"`), so a report
  can state the calendar its numbers used, as it states `periods_per_year`.
- **A default, set by the fund.** Most funds use one calendar; `set_default_calendar` sets it at start-up,
  and functions that take `calendar=None` (`align_to_calendar`, `session_gaps`, and later the horizons and
  data checks) use it. A fund on several exchanges uses `using_calendar` per block, a `ContextVar`, so
  threads and tasks do not see each other's choice, or passes `joint(...)` explicitly. With nothing set,
  the default is weekdays under a name that says so. A firm may subclass `SessionCalendar` to override
  its methods; `as_calendar` and the default keep the subclass.
- **Sessions are days.** Early closes, intraday hours, and day counts are out of scope.
  `sessions_per_year` is read beside a stated `periods_per_year`; it does not replace it.

## Alternatives considered

- **Depend on `pandas_market_calendars` or `exchange_calendars`.** Either would make pandas a requirement
  of a numpy-only package, and neither covers a firm's own settlement or fund-administrator calendar.
  The firm installs the one it trusts; the toolbox reads it.
- **Build on pandas `CustomBusinessDay`.** It is a `numpy.busdaycalendar` with pandas around it. The
  toolbox uses numpy directly and reads a `CustomBusinessDay` it is given.
- **Holiday rules of our own.** Keeping exchange rules is the libraries' work, and a stale copy would be
  wrong without saying so.
- **A calendar argument everywhere, with no default.** Explicit, but a fund passes the same calendar to
  every call, and the one call that forgets it uses weekdays silently. A default set once, and named in
  reports, is harder to get wrong.
- **A protocol only, with free functions over it.** Each call would ask the source for sessions again; a
  materialised array is a search, and a century of sessions is about 25,000 days.

## Consequences

- Synthetic data, data checks (rows on non-sessions, missing sessions), horizons (month ends and coverage),
  and backtest rebalance dates can take a calendar. Those follow in later changes; `business_days` stays
  until then, documented as weekdays only.
- The adapters are tested with fakes of the two libraries' interfaces, offline; pandas' rules are tested
  for real when pandas is installed. A change in either library's method names would break only its adapter.
