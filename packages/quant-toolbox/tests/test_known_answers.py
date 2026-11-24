"""Known answers: each expected value is worked out by hand in the test, never by this package.

Both metrics backends run every test (see conftest.py), so each figure here holds on either one.
Conventions under test: a return dated d covers the bar since the previous one; annualised return
is geometric; volatility is the sample standard deviation times the square root of the periods;
the Sharpe ratio takes an annual risk-free rate divided by the periods a year.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from quant_toolbox.performance import (
    annualized_return,
    annualized_volatility,
    drawdowns,
    horizon_return,
    max_drawdown,
    sharpe_ratio,
)


def month_ends(start_year: int, start_month: int, count: int) -> list[dt.date]:
    """Month-end dates, ``count`` of them, from the given month."""
    days = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    dates = []
    year, month = start_year, start_month
    for _ in range(count):
        dates.append(dt.date(year, month, days[month]))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return dates


def test_annualised_return_of_a_monthly_series_is_geometric() -> None:
    # Twelve months of 1%: 1.01 ** 12 is 1.12682503013, so the annual return is 12.682503013%.
    returns = np.full(12, 0.01)

    assert annualized_return(returns, 12) == pytest.approx(0.12682503013, abs=1e-10)


def test_annualised_volatility_of_a_monthly_series() -> None:
    # Returns alternate 2% and 0%: mean 1%, every deviation 1%, so the sum of squared deviations
    # is 12 * 0.0001 = 0.0012 and the sample variance is 0.0012 / 11. Annualised by 12.
    returns = np.array([0.02, 0.0] * 6)

    assert annualized_volatility(returns, 12) == pytest.approx(math.sqrt(12 * 0.0012 / 11))


def test_annualised_return_of_a_daily_series_scales_by_the_252_periods() -> None:
    # One bar doubles the money and the other 251 are flat: growth is 2 over 252 bars, which is
    # exactly one year at 252 periods, so the annual return is 100%.
    returns = np.zeros(252)
    returns[100] = 1.0

    assert annualized_return(returns, 252) == pytest.approx(1.0, abs=1e-12)


def test_annualised_volatility_of_a_daily_series() -> None:
    # 252 bars alternate +1% and -1%: mean 0, sum of squares 252 * 0.0001 = 0.0252, sample
    # variance 0.0252 / 251, annualised by 252.
    returns = np.array([0.01, -0.01] * 126)

    assert annualized_volatility(returns, 252) == pytest.approx(
        math.sqrt(252 * 0.0252 / 251), abs=1e-12
    )


def test_itd_on_a_partial_year_is_cumulative_not_annualised() -> None:
    # Six months of 1% is under a year, so ITD is the plain compounded return: 1.01 ** 6 - 1.
    dates = month_ends(2024, 1, 6)
    returns = np.full(6, 0.01)

    assert horizon_return(dates, returns, "ITD", 12) == pytest.approx(0.06152015056, abs=1e-10)


def test_ytd_and_qtd_across_a_year_end() -> None:
    # 2024 has twelve months of 1%; 2025 has 2% a month from January. As of 31 May 2025:
    # QTD starts at 31 March 2025, so April and May remain: 1.02 ** 2 - 1 = 4.04%.
    # YTD starts at 31 December 2024, so January to May remain: 1.02 ** 5 - 1 = 10.40808032%.
    dates = month_ends(2024, 1, 17)  # January 2024 .. May 2025
    returns = np.array([0.01] * 12 + [0.02] * 5)

    assert horizon_return(dates, returns, "QTD", 12) == pytest.approx(0.0404, abs=1e-12)
    assert horizon_return(dates, returns, "YTD", 12) == pytest.approx(0.1040808032, abs=1e-10)


def test_max_drawdown_and_its_dates() -> None:
    # Wealth from 1: up 10% to 1.10 (peak, 31 January), down 20% to 0.88, down 10% to 0.792
    # (trough, 31 March), up 5% to 0.8316, up 30% to 1.08108. Never back above 1.10.
    # The drawdown is 0.792 / 1.10 - 1 = -28%.
    dates = month_ends(2024, 1, 5)
    returns = np.array([0.10, -0.20, -0.10, 0.05, 0.30])

    assert max_drawdown(returns) == pytest.approx(-0.28, abs=1e-12)
    worst = drawdowns(dates, returns)[0]
    assert worst.peak == dates[0]
    assert worst.trough == dates[2]
    assert worst.recovered is None
    assert worst.depth == pytest.approx(-0.28, abs=1e-12)


def test_sharpe_ratio_with_a_risk_free_rate() -> None:
    # Returns alternate 2% and 0%: mean 1%, sample standard deviation sqrt(0.0012 / 11).
    # A 6% annual risk-free rate is 0.5% a month, so the excess mean is 0.5% and the Sharpe ratio
    # is 0.005 / sqrt(0.0012 / 11) * sqrt(12) = 0.005 * sqrt(110000), about 1.658.
    returns = np.array([0.02, 0.0] * 6)

    assert sharpe_ratio(returns, 12, 0.06) == pytest.approx(0.005 * math.sqrt(110000), abs=1e-9)
    assert sharpe_ratio(returns, 12, 0.0) == pytest.approx(0.01 * math.sqrt(110000), abs=1e-9)
