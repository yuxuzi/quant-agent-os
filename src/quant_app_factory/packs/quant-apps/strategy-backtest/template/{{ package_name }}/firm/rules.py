"""Your firm's own portfolio rules. Generated once; later template updates leave this file alone.

A rule turns signal scores into target weights (bars by assets, as fractions of NAV). Weights at
bar t may use prices and scores up to bar t only. The backtest lags, drifts, and costs them. A
firm's optimizer can also write a weights file instead: set ``weights_file`` in ``config.toml``.
For example::

    import numpy as np

    from ..metrics import FloatArray
    from ..parameters import Parameters


    def top_ten_equal(prices: FloatArray, signal: FloatArray, parameters: Parameters) -> FloatArray:
        \"\"\"Equal weights on the ten highest scores.\"\"\"
        weights = np.zeros(signal.shape)
        for bar in range(signal.shape[0]):
            valid = np.flatnonzero(np.isfinite(signal[bar]))
            top = valid[np.argsort(signal[bar, valid])[-10:]]
            if top.size:
                weights[bar, top] = 1.0 / top.size
        return weights


    RULES = {"top_ten_equal": top_ten_equal}

Then set ``exposure = "top_ten_equal"`` in ``config.toml``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..metrics import FloatArray

RULES: dict[str, Callable[[FloatArray, FloatArray, Any], FloatArray]] = {}
