"""Carried-forward facts: exact zero runs, the share of zeros, and weekdays every asset is zero."""

from __future__ import annotations

import datetime as dt

from quant_toolbox.data import carried_forward


def test_a_run_of_three_exact_zeros_flags_the_asset() -> None:
    found = carried_forward({"A": [0.01, 0.0, 0.0, 0.0, 0.02], "B": [0.01, -0.02, 0.03, 0.0, 0.01]})

    assert [
        (item.asset, item.zero_returns, item.window, item.longest_run) for item in found.assets
    ] == [("A", 3, 5, 3)]
    assert found.closed_days == []


def test_two_zeros_that_are_over_five_percent_of_the_window_flag_the_asset() -> None:
    values = [0.01] * 30 + [0.0, 0.01, 0.0]  # two zeros in 33 is over 5%, and never in a row

    found = carried_forward({"A": values})

    assert [(item.zero_returns, item.window, item.longest_run) for item in found.assets] == [
        (2, 33, 1)
    ]


def test_a_single_zero_flags_nothing() -> None:
    assert carried_forward({"A": [0.01, 0.0, 0.02, 0.03]}).assets == []


def test_an_empty_window_flags_nothing() -> None:
    assert carried_forward({"A": []}).assets == []


def test_a_weekday_with_zero_for_every_asset_is_a_closed_day() -> None:
    dates = [dt.date(2024, 12, 2), dt.date(2024, 12, 3), dt.date(2024, 12, 4)]  # Mon, Tue, Wed

    found = carried_forward({"A": [0.01, 0.0, 0.02], "B": [0.0, 0.0, 0.03]}, dates)

    assert found.closed_days == [dt.date(2024, 12, 3)]


def test_a_weekend_zero_is_not_a_closed_day_and_mismatched_dates_are_not_checked() -> None:
    weekend = [dt.date(2024, 12, 7)]  # a Saturday
    assert carried_forward({"A": [0.0]}, weekend).closed_days == []
    assert carried_forward({"A": [0.0, 0.0]}, weekend).closed_days == []
