"""Portfolio construction helpers."""

import numpy as np
import pandas as pd

from kestrel_quant.config import CONFIG, log


def vol_target_weights(returns, signal=None, *args, **kwargs):
    """Scale each name to the same ex-ante vol, then the book to the target vol.

    returns: wide daily returns. signal: wide frame of the same shape (defaults to all long).
    kwargs: target, window, max_leverage (see CONFIG["vol_target"]).
    """
    opts = dict(CONFIG["vol_target"])
    opts.update(kwargs)
    ann = np.sqrt(CONFIG["annualization"])
    vol = returns.rolling(opts["window"]).std() * ann
    if signal is None:
        signal = pd.DataFrame(1.0, index=returns.index, columns=returns.columns)
    signal = signal.reindex_like(returns).fillna(0.0)
    raw = np.sign(signal) / vol.replace(0, np.nan)
    raw = raw.div(raw.abs().sum(axis=1), axis=0)
    book = (raw.shift(1) * returns).sum(axis=1)
    book_vol = book.rolling(opts["window"]).std() * ann
    scale = (opts["target"] / book_vol).clip(upper=opts["max_leverage"])
    weights = raw.mul(scale, axis=0).dropna(how="all").fillna(0.0)
    if args:
        log("ignoring extra args", args)
    log("vol target", opts["target"], "mean gross", round(weights.abs().sum(axis=1).mean(), 2))
    return weights


def backtest(weights, returns, cost_bps=0):
    w = weights.reindex_like(returns).fillna(0.0)
    gross = (w.shift(1) * returns).sum(axis=1)
    turnover = w.diff().abs().sum(axis=1)
    return gross - turnover * cost_bps / 1e4
