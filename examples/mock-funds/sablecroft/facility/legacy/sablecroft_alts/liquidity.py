"""Liquidity ladder from redemption terms."""

import datetime

import pandas as pd

from sablecroft_alts.config import log

BUCKETS = [30, 90, 180, 365, 730]


def liquidity_ladder(terms, as_of=None):
    """terms: DataFrame by manager with lockup_months, notice_days, redemption_freq_months,
    gate_pct,
    nav_usd, investment_date. Returns USD redeemable by bucket (days from as_of)."""
    as_of = as_of or datetime.date.today()  # global clock: results change day to day
    out = {b: 0.0 for b in [*BUCKETS, "later"]}
    for _mid, row in terms.iterrows():
        start = pd.Timestamp(row.investment_date) + pd.DateOffset(months=int(row.lockup_months))
        start = max(start, pd.Timestamp(as_of)) + pd.Timedelta(days=int(row.notice_days))
        step = int(row.redemption_freq_months)
        left = row.nav_usd
        day = start
        while left > 1:
            days = (day - pd.Timestamp(as_of)).days
            part = min(left, row.nav_usd * row.gate_pct)
            key = next((b for b in BUCKETS if days <= b), "later")
            out[key] += part
            left -= part
            day = day + pd.DateOffset(months=step)
    log("ladder", out)
    return out
