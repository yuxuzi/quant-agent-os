"""Untyped pandas code reading module configuration and printing: needs a facade."""

import pandas as pd

CONFIG = {"lookback": 20, "cap": 3.0}


def carry_signal(prices, lookback=None):
    lookback = lookback or CONFIG["lookback"]
    print("computing carry over", lookback, "days")
    momentum = prices.pct_change(lookback)
    risk = prices.pct_change().rolling(lookback).std()
    return (momentum / risk).clip(-CONFIG["cap"], CONFIG["cap"])


def _zscore(frame):
    return (frame - frame.mean()) / frame.std()


def cross_section(prices: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-scores of each asset's one-period return."""
    return _zscore(prices.pct_change())
