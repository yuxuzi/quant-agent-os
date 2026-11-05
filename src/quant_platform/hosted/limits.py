"""Plan limits checked before a call starts, never during it (decision 0015).

An admitted call runs to the end, even if it takes the tenant over its quota.

`PlanLimits` enforces a rate limit (a token bucket in this process, so N replicas admit up to N
times the rate) and a monthly quota of billable calls. The monthly count comes from a counter
over the shared usage events, re-read at most every ``refresh_seconds`` and advanced locally
for each admitted call in between, so concurrent calls can overshoot by at most the calls in
flight.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from quant_agent_os.runtime import Refusal

# Counts a tenant's billable calls from ``start`` up to ``end``.
BillableCounter = Callable[[datetime, datetime], int]
REFRESH_SECONDS = 30.0


class TokenBucket:
    """``rate`` calls a minute, with bursts of up to ``rate``; safe to share between threads."""

    def __init__(self, rate: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if rate <= 0:
            raise ValueError("A rate limit needs at least one call a minute.")
        self.rate = rate
        self._clock = clock
        self._tokens = float(rate)
        self._at = clock()
        self._lock = threading.Lock()

    def take(self) -> float:
        """Take one token and return 0, or return the seconds until one is available."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.rate, self._tokens + (now - self._at) * self.rate / 60)
            self._at = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            return (1 - self._tokens) * 60 / self.rate


def month_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the UTC calendar month containing ``now``, as a half-open window."""
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


class PlanLimits:
    """One tenant's rate limit and monthly quota; an unset limit is not checked."""

    def __init__(
        self,
        tenant: str,
        *,
        calls_per_minute: int | None = None,
        monthly_calls: int | None = None,
        count_billable: BillableCounter | None = None,
        refresh_seconds: float = REFRESH_SECONDS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if monthly_calls is not None and count_billable is None:
            raise ValueError("A monthly quota needs a counter of billable calls.")
        self.tenant = tenant
        self.monthly_calls = monthly_calls
        self.bucket = TokenBucket(calls_per_minute, clock=clock) if calls_per_minute else None
        self._count = count_billable
        self._refresh = refresh_seconds
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._month: datetime | None = None
        self._used = 0
        self._read_at = -math.inf

    def admit(self, tool: str) -> Refusal | None:
        """Refuse a call over the monthly quota or the rate limit; otherwise count it."""
        if self.monthly_calls is not None:
            refusal = self._check_month()
            if refusal is not None:
                return refusal
        if self.bucket is not None:
            wait = self.bucket.take()
            if wait > 0:
                if self.monthly_calls is not None:
                    with self._lock:
                        self._used -= 1  # not admitted after all
                return Refusal(
                    f"Tenant {self.tenant} is over its {self.bucket.rate} calls a minute; "
                    f"retry in {math.ceil(wait)} s.",
                    math.ceil(wait),
                )
        return None

    def _check_month(self) -> Refusal | None:
        assert self.monthly_calls is not None and self._count is not None
        now = self._now()
        start, end = month_window(now)
        with self._lock:
            stale = self._clock() - self._read_at >= self._refresh
            if self._month != start or stale:
                self._used = self._count(start, end)
                self._month, self._read_at = start, self._clock()
            if self._used >= self.monthly_calls:
                return Refusal(
                    f"Tenant {self.tenant} used its {self.monthly_calls:,} calls for "
                    f"{start:%B %Y}; the quota resets {end:%Y-%m-%dT%H:%MZ}.",
                    max(1, math.ceil((end - now).total_seconds())),
                )
            self._used += 1
            return None
