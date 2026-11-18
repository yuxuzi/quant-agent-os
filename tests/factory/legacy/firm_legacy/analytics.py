"""Typed, documented, pure: exposed as it is."""

from __future__ import annotations

import math

TRADING_DAYS = 252


def annualised_volatility(returns: list[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised volatility of periodic returns: the sample standard deviation, scaled.

    Args:
        returns: Periodic simple returns, as fractions.
        periods_per_year: Periods in a year, such as 252 for daily returns.
    """
    values = [value for value in returns if not math.isnan(value)]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance * periods_per_year)
