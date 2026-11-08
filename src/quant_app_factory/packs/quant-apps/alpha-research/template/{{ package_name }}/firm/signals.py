"""Your firm's own signals. Generated once; later template updates leave this file alone.

A signal takes prices (bars by assets) and returns scores of the same shape, with NaN where there
is none. Scores at bar t may use prices up to bar t only: `signals.look_ahead_bars` tests that,
and every run of a firm signal uses it. For example::

    import numpy as np

    from ..metrics import FloatArray


    def low_volatility(prices: FloatArray) -> FloatArray:
        \"\"\"Minus the volatility of the last 60 bars: quieter assets score higher.\"\"\"
        returns = prices[1:] / prices[:-1] - 1.0
        scores = np.full(prices.shape, np.nan)
        for bar in range(60, prices.shape[0]):
            scores[bar] = -np.std(returns[bar - 60 : bar], axis=0)
        return scores


    SIGNALS = {"low_volatility": low_volatility}

Then set ``signal = "low_volatility"`` in ``config.toml``. The template holds no firm's alpha.
"""

from __future__ import annotations

from collections.abc import Callable

from ..metrics import FloatArray

SIGNALS: dict[str, Callable[[FloatArray], FloatArray]] = {}
