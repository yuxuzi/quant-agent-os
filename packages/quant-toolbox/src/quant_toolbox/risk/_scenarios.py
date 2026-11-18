"""Historical scenarios: what today's book would have returned over a past stress window.

Conventions:
- A window is ``(start, end)``: ``start`` is the last close before the stress, ``end`` its last
  day. Each asset's return is compounded over the bars dated after ``start``, up to and including
  ``end``; a missing return counts as 0 (no move).
- The book's return is linear, ``sum(w_i * shock_i)``, as in `stress_return`: today's weights,
  held through the window without rebalancing or second-order effects.
- A window the dates do not cover (the first date after ``start``, or the last before ``end``)
  gives None, never a shorter window under the same name.
- `SCENARIOS` are conventional windows for well-known US equity stress episodes, as commonly
  dated by index closes; they are not official definitions, and a firm should set its own.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import numpy as np

from quant_toolbox._types import FloatArray
from quant_toolbox.risk._book import stress_return

_D = dt.date
SCENARIOS: dict[str, tuple[dt.date, dt.date]] = {
    "Quant quake (Aug 2007)": (_D(2007, 8, 3), _D(2007, 8, 10)),
    "Lehman (Sep-Oct 2008)": (_D(2008, 9, 12), _D(2008, 10, 10)),
    "Momentum crash (Mar-May 2009)": (_D(2009, 3, 9), _D(2009, 5, 29)),
    "Flash crash (May 2010)": (_D(2010, 5, 5), _D(2010, 5, 6)),
    "US downgrade (Aug 2011)": (_D(2011, 7, 22), _D(2011, 8, 8)),
    "Taper tantrum (2013)": (_D(2013, 5, 21), _D(2013, 6, 24)),
    "Volmageddon (Feb 2018)": (_D(2018, 2, 1), _D(2018, 2, 8)),
    "Q4 2018 selloff": (_D(2018, 9, 20), _D(2018, 12, 24)),
    "COVID crash (2020)": (_D(2020, 2, 19), _D(2020, 3, 23)),
    "Rate shock (H1 2022)": (_D(2022, 1, 3), _D(2022, 6, 16)),
    "Regional banks (Mar 2023)": (_D(2023, 3, 8), _D(2023, 3, 17)),
    "Yen carry unwind (Aug 2024)": (_D(2024, 7, 31), _D(2024, 8, 5)),
}


def scenario_shocks(
    dates: Sequence[dt.date], asset_returns: FloatArray, start: dt.date, end: dt.date
) -> FloatArray | None:
    """Each asset's compounded return over the bars dated after ``start`` up to ``end``.

    ``asset_returns`` is bars by assets, one row per date. None when the dates do not cover the
    window: no bar on or before ``start``, or none on or after ``end``.
    """
    if end <= start:
        raise ValueError(f"A scenario ends after it starts; got {start} to {end}.")
    values = np.asarray(asset_returns, dtype=np.float64)
    if values.ndim == 1:
        values = values[:, None]
    if values.shape[0] != len(dates):
        raise ValueError("asset_returns needs one row per date.")
    if not dates or dates[0] > start or dates[-1] < end:
        return None
    inside = np.array([start < day <= end for day in dates], dtype=bool)
    window = np.nan_to_num(values[inside])
    result: FloatArray = np.prod(1.0 + window, axis=0) - 1.0
    return result


def historical_stress(
    weights: FloatArray,
    dates: Sequence[dt.date],
    asset_returns: FloatArray,
    scenarios: Mapping[str, tuple[dt.date, dt.date]] | None = None,
) -> dict[str, float | None]:
    """Today's book over each scenario window (`SCENARIOS` by default); None where not covered."""
    chosen = SCENARIOS if scenarios is None else scenarios
    results: dict[str, float | None] = {}
    for name, (start, end) in chosen.items():
        shocks = scenario_shocks(dates, asset_returns, start, end)
        results[name] = None if shocks is None else stress_return(weights, shocks)
    return results
