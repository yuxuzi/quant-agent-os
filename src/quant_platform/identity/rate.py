"""A token bucket, so that unauthenticated requests cannot make the server do unbounded work."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


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
