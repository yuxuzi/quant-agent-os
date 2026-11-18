"""Liquidity: days to liquidate each position, and how much of the book is out in how long.

Conventions:
- Positions and average daily volume (ADV) are money in one currency: a position's market value
  (shorts negative) and the value an asset trades on an average day.
- Each position is sold at ``participation`` of its ADV every day, all positions in parallel, so
  days to liquidate is ``|position| / (participation * adv)``. No price impact, no ADV change.
- The share liquidated within d days is ``sum(min(|p|, d * participation * adv)) / gross``: a
  position counts as sold pro rata, day by day. Gross is the sum of absolute positions.
- Buckets follow Form PF's liquidity horizons by default (`LIQUIDITY_BUCKETS`: 1 day or less,
  2-7, 8-30, 31-90, 91-180, 181-365, more than 365 days). Bucket shares are not cumulative; with
  the share of positions that have no positive ADV ("no ADV"), they sum to one.
- A book with no gross exposure has every share 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

LIQUIDITY_BUCKETS = (1, 7, 30, 90, 180, 365)
NO_ADV = "no ADV"


@dataclass(frozen=True)
class Liquidity:
    """How fast a book can be sold at a share of each asset's average daily volume."""

    days: dict[str, float]  # days to liquidate, for each asset with a positive ADV
    within: dict[int, float]  # share of gross sold within each horizon, cumulative
    buckets: dict[str, float]  # share of gross by bucket, not cumulative; sums to one
    no_adv: list[str]  # positions held with no positive ADV, which cannot be timed
    gross: float
    participation: float


def liquidity(
    positions: Mapping[str, float],
    adv: Mapping[str, float],
    participation: float = 0.20,
    buckets: Sequence[int] = LIQUIDITY_BUCKETS,
) -> Liquidity:
    """Days to liquidate each position at ``participation`` of its ADV, and the book by bucket.

    An asset missing from ``adv``, or with an ADV that is not positive, is listed in ``no_adv``
    and counted in the "no ADV" bucket.
    """
    if not 0.0 < participation <= 1.0:
        raise ValueError(f"participation is a share of ADV in (0, 1]; got {participation}.")
    horizons = list(buckets)
    if not horizons or horizons[0] < 1 or any(b <= a for a, b in pairwise(horizons)):
        raise ValueError(f"buckets must be ascending whole days from 1; got {horizons}.")
    sizes = {asset: abs(float(value)) for asset, value in positions.items()}
    gross = sum(size for size in sizes.values() if math.isfinite(size))
    days: dict[str, float] = {}
    no_adv: list[str] = []
    for asset, size in sizes.items():
        if not math.isfinite(size) or size == 0.0:
            continue
        volume = float(adv.get(asset, math.nan))
        if math.isfinite(volume) and volume > 0.0:
            days[asset] = size / (participation * volume)
        else:
            no_adv.append(asset)

    def sold(horizon: int) -> float:
        if gross <= 0.0:
            return 0.0
        total = sum(min(sizes[a], horizon * participation * float(adv[a])) for a in days)
        return total / gross

    within = {horizon: sold(horizon) for horizon in horizons}
    timed = sum(sizes[asset] for asset in days) / gross if gross > 0.0 else 0.0
    shares: dict[str, float] = {}
    previous = 0.0
    for index, horizon in enumerate(horizons):
        if index:
            label = f"{horizons[index - 1] + 1}-{horizon} days"
        else:
            label = "1 day or less" if horizon == 1 else f"{horizon} days or less"
        shares[label] = within[horizon] - previous
        previous = within[horizon]
    shares[f"more than {horizons[-1]} days"] = timed - previous
    shares[NO_ADV] = sum(sizes[asset] for asset in no_adv) / gross if gross > 0.0 else 0.0
    return Liquidity(
        days=days,
        within=within,
        buckets=shares,
        no_adv=no_adv,
        gross=gross,
        participation=participation,
    )
