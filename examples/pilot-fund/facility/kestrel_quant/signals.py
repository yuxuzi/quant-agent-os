"""Signals the desks trade. All return a wide frame, dates x instrument_id, z-scored per date."""

import numpy as np

from kestrel_quant import db
from kestrel_quant.config import CONFIG, log


def _zscore(df):
    mean = df.mean(axis=1)
    std = df.std(axis=1).replace(0, np.nan)
    return df.sub(mean, axis=0).div(std, axis=0)


def momentum_signal(prices=None, lookback=None, skip=None):
    """12-1 style price momentum: return from t-lookback to t-skip, z-scored across names."""
    lookback = lookback or CONFIG["momentum"]["lookback"]
    skip = skip if skip is not None else CONFIG["momentum"]["skip"]
    if prices is None:
        prices = db.load_prices()
    raw = prices.shift(skip) / prices.shift(lookback) - 1.0
    log("momentum", lookback, skip, "names:", prices.shape[1])
    return _zscore(raw.dropna(how="all"))


def carry_signal(asset_class=None, **kwargs):
    winsor = kwargs.get("winsor", CONFIG["carry"]["winsor"])
    carry = db.load_signal("carry")
    if asset_class:
        names = db.instruments(asset_class=asset_class).instrument_id
        carry = carry[[c for c in carry.columns if c in set(names)]]
    return _zscore(carry).clip(-winsor, winsor)


def value_zscore(values=None, by=None):
    """Book-to-price z-scored within each region (or sector), equities only."""
    by = by or CONFIG["value"]["by"]
    if values is None:
        values = db.load_signal("value")
    groups = db.instruments(asset_class="equity").set_index("instrument_id")[by]
    out = values.copy() * np.nan
    for _, members in groups.groupby(groups):
        cols = [c for c in members.index if c in values.columns]
        out[cols] = _zscore(values[cols])
    return out


def combine(*signals, weights=None):
    weights = weights or [1.0 / len(signals)] * len(signals)
    total = None
    for sig, w in zip(signals, weights, strict=True):
        part = sig.fillna(0.0) * w
        total = part if total is None else total.add(part, fill_value=0.0)
    return total
