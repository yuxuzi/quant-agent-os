"""Return-series helpers: unsmoothing and the Lo Sharpe adjustment."""

import numpy as np
import pandas as pd

from sablecroft_alts.config import CONFIG, log


def load_returns(vintage="final"):
    """Wide monthly returns for every monthly manager from the curated Delta table."""
    path = CONFIG["lake"] + "/curated/mgr_returns"
    df = pd.read_parquet(path)
    df = df[df.vintage == vintage]
    wide = df.pivot(index="period_end", columns="manager_id", values="return_local")
    wide.index = pd.to_datetime(wide.index)
    log("loaded", vintage, wide.shape)
    return wide


def unsmooth(series, order=2):
    """Geltner-style unsmoothing with an AR(order) filter fitted on the series itself."""
    r = series.dropna()
    rho = [r.autocorr(k) for k in range(1, order + 1)]
    out = r.copy()
    for k, p in enumerate(rho, start=1):
        out = (out - p * out.shift(k)) / (1 - p)
    log("rho", rho)
    return out.dropna()


def lo_sharpe(series, q=12):
    r = series.dropna()
    sr = (r.mean() - CONFIG["risk_free"] / q) / r.std()
    acs = [r.autocorr(k) for k in range(1, q)]
    eta = q / np.sqrt(q + 2 * sum((q - k) * a for k, a in enumerate(acs, start=1)))
    print("raw annual sharpe", sr * np.sqrt(q), "lo-adjusted", sr * eta)
    return sr * eta
