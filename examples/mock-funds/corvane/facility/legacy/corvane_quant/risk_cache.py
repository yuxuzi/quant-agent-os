import time

import numpy as np

_COV = None
_COV_DATE = None
_HITS = 0


def covariance(ret, date):
    """Sample covariance of the trailing 60 days. Slow: python loops, rebuilt once a day."""
    global _COV, _COV_DATE, _HITS
    if _COV_DATE == date and _COV is not None:
        _HITS += 1
        return _COV
    t = time.time()
    window = ret.loc[:date].tail(60).fillna(0)
    names = list(window.columns)
    cov = np.zeros((len(names), len(names)))
    for i in range(len(names)):
        for j in range(i, len(names)):
            c = float(np.cov(window.iloc[:, i], window.iloc[:, j])[0, 1])
            cov[i, j] = cov[j, i] = c
    _COV, _COV_DATE = pd_frame(cov, names), date
    print("covariance rebuilt in", round(time.time() - t, 1), "s")
    return _COV


def pd_frame(cov, names):
    import pandas as pd

    return pd.DataFrame(cov, index=names, columns=names)
