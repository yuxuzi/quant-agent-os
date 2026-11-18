"""Private-markets maths: XIRR, multiples, and PME."""

import pandas as pd


def xirr(flows):
    """flows: DataFrame with columns date, amount. Newton with a fixed start, as the old Excel
    did."""
    t = (pd.to_datetime(flows["date"]) - pd.to_datetime(flows["date"]).min()).dt.days / 365.0
    a = flows["amount"].values
    r = 0.1
    for _ in range(200):
        f = (a / (1 + r) ** t).sum()
        d = (-t * a / (1 + r) ** (t + 1)).sum()
        if abs(d) < 1e-12:
            break
        r -= f / d
        if r <= -0.99:
            r = -0.9
    return r


def fund_multiples(calls, dists, nav):
    paid = sum(calls)
    print("tvpi", (sum(dists) + nav) / paid)
    return {"tvpi": (sum(dists) + nav) / paid, "dpi": sum(dists) / paid, "rvpi": nav / paid}


def ks_pme(flows, index, nav):
    """flows: date, amount (calls negative). index: Series of levels by date."""
    end = index.iloc[-1]
    fv = lambda d: end / index[:d].iloc[-1]  # noqa: E731
    dist = sum(a * fv(d) for d, a in zip(flows["date"], flows["amount"], strict=True) if a > 0)
    call = sum(-a * fv(d) for d, a in zip(flows["date"], flows["amount"], strict=True) if a < 0)
    return (dist + nav) / call
