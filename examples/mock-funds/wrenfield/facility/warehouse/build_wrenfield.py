# ruff: noqa: E501
"""Build Wrenfield Credit Opportunities' cloud estate as local stand-ins: all data is fictional.

    python build_wrenfield.py [ROOT] [--seed N] [--format parquet|csv]

ROOT holds what AWS would hold, with the same names and key layouts:

- ``s3/wrenfield-data-lake/curated/<table>/dt=YYYY-MM-DD/part-0000.parquet``: vendor prices, CDS,
  and rates, curated for Athena (Hive partitions); ``raw/`` holds one vendor file as it landed;
  ``catalog/`` is the Glue catalog JSON and ``athena/`` holds the views.
- ``s3/wrenfield-research/credit/``: the research documents (copied in by the caller).
- ``rds/wrenfield_oms.sqlite``: the order-management database. On AWS it is RDS PostgreSQL;
  SQLite stands in for it, with the same table and column names (see site/sql/wrenfield_oms.sql).
- ``uri_map.json``: which URI is which directory, and ``known_answers.json``: the planted truths.

Standard library plus pyarrow when it is installed (CSV with the same layout otherwise) and
numpy for the rank correlation. The same seed gives the same contents.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_SEED = 20261118
START, END = date(2025, 8, 11), date(2025, 12, 31)
LAKE, RESEARCH = "wrenfield-data-lake", "wrenfield-research"
OMS_URI = "rds://wrenfield-oms.cluster-c1x2y3.us-east-1.rds.amazonaws.com:5432/oms"
CAPITAL = {"hy_ls": 400e6, "ig_rv": 600e6, "distressed": 200e6}
FUND_CAPITAL = sum(CAPITAL.values())
BOOKS = ("hy_ls", "ig_rv", "distressed", "overlay")
SCALE = [
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC+",
    "CCC",
    "CCC-",
    "CC",
    "C",
    "D",
]  # score = position + 1; IG is 1..10
SECTORS = {
    "Energy": 1, "Consumer": 2, "Healthcare": 3, "Technology": 4, "Industrials": 5,
    "Financials": 6, "Media": 7, "Utilities": 8, "Materials": 9, "RealEstate": 10,
}  # fmt: skip
SENIORITY = {1: "senior secured", 2: "senior unsecured", 3: "subordinated"}
SENIORITY_MULT = {1: 0.85, 2: 1.0, 3: 1.35}
RECOVERY_ASSUMED = {1: 0.60, 2: 0.40, 3: 0.15}
TENORS = {"3Y": 3.0, "5Y": 5.0, "10Y": 10.0}
CDS_RATE, CDS_RECOVERY = 0.04, 0.40
INDICES = {"CDXLIKE_IG": 55.0, "CDXLIKE_HY": 380.0, "ITXLIKE_MAIN": 62.0, "ITXLIKE_XOVER": 290.0}
CURVE_TENORS = (1.0, 2.0, 5.0, 10.0, 30.0)
BASE_CURVE = {
    "USD": (4.00, 3.95, 4.05, 4.30, 4.80),
    "EUR": (2.20, 2.30, 2.50, 2.75, 3.10),
}  # percent
FIRST = [
    "Alder",
    "Brack",
    "Calder",
    "Dunmore",
    "Elm",
    "Fenwick",
    "Garrow",
    "Harlow",
    "Ivor",
    "Jessup",
    "Kellan",
    "Larch",
    "Norwood",
    "Oakham",
    "Pryor",
    "Quill",
    "Rowan",
    "Sable",
    "Thorne",
    "Umber",
    "Vale",
    "Whit",
    "Yarrow",
    "Zephyr",
    "Ashby",
    "Bram",
    "Cove",
    "Dell",
    "Haldane",
    "Merrow",
    "Penrose",
    "Tarn",
    "Wexley",
    "Lorne",
    "Greer",
    "Foxton",
    "Ingram",
    "Selby",
    "Tolland",
    "Wyck",
    "Barrow",
    "Corbin",
    "Denholm",
    "Eskdale",
    "Finlay",
    "Gresham",
    "Hawke",
    "Irwell",
    "Kestle",
    "Lyle",
    "Mortlake",
    "Nairn",
    "Orton",
    "Presley",
    "Radley",
    "Stanton",
    "Truro",
    "Upton",
    "Verne",
    "Wilmot",
    "Yately",
    "Ainslie",
    "Beck",
    "Crewe",
    "Dacre",
    "Eden",
    "Fairlie",
    "Galway",
    "Hythe",
    "Islay",
    "Jura",
    "Kinver",
    "Leith",
    "Maple",
    "Newry",
    "Oban",
    "Perth",
    "Rye",
    "Skye",
    "Tay",
]
SUFFIX = {
    "Energy": ("Offshore", "Petroleum", "Drilling"),
    "Consumer": ("Retail", "Foods", "Brands"),
    "Healthcare": ("Health", "Pharma", "Care"),
    "Technology": ("Systems", "Software", "Networks"),
    "Industrials": ("Industries", "Logistics", "Aerospace"),
    "Financials": ("Capital", "Finance", "Credit"),
    "Media": ("Media", "Telecom", "Broadcast"),
    "Utilities": ("Utilities", "Power", "Water"),
    "Materials": ("Chemicals", "Steel", "Packaging"),
    "RealEstate": ("Realty", "Properties", "REIT"),
}


def business_days(start: date = START, end: date = END) -> list[date]:
    """Return the Monday-to-Friday dates from ``start`` to ``end`` inclusive."""
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def add_months(day: date, months: int) -> date:
    """Return ``day`` moved by whole months; the day of month must be 28 or less."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, day.day)


def days_360(a: date, b: date) -> int:
    """Return the 30/360 day count from ``a`` to ``b``."""
    return 360 * (b.year - a.year) + 30 * (b.month - a.month) + (min(b.day, 30) - min(a.day, 30))


def interpolate(tenors: tuple[float, ...], values: tuple[float, ...], x: float) -> float:
    """Return the value at ``x`` by linear interpolation, flat beyond the ends."""
    if x <= tenors[0]:
        return values[0]
    for (t0, v0), (t1, v1) in zip(
        zip(tenors, values, strict=False), zip(tenors[1:], values[1:], strict=True), strict=False
    ):
        if x <= t1:
            return v0 + (v1 - v0) * (x - t0) / (t1 - t0)
    return values[-1]


def risky_annuity(spread_bp: float, years: float = 5.0) -> float:
    """Return the CDS risky annuity: (1 - exp(-(r + h) T)) / (r + h), h = s / (1 - R)."""
    rate = CDS_RATE + spread_bp / 1e4 / (1.0 - CDS_RECOVERY)
    return (1.0 - math.exp(-rate * years)) / rate


@dataclass
class Bond:
    """A bond's terms."""

    bond_id: str
    issuer: str
    currency: str
    coupon: float
    maturity: date
    seniority: int
    first_call: date | None = None
    call_price: float | None = None
    recovery: float | None = None  # planted, for the two defaulters
    book: str = ""


@dataclass
class Issuer:
    """An issuer and its spread path."""

    ticker: str
    name: str
    sector: str
    score: int  # rating score at the start of the window
    sigma: float
    beta: float
    default_on: date | None = None
    default_ramp_from: date | None = None
    default_ramp: float = 1.0
    cds: bool = False
    basis: float = 0.0
    events: list[tuple[date, int]] = field(default_factory=list)  # in-window rating changes
    jumps: dict[date, float] = field(default_factory=dict)


def _workouts(bond: Bond, asof: date) -> list[tuple[date, float]]:
    out = [(bond.maturity, 100.0)]
    if bond.first_call is not None and bond.call_price is not None:
        if bond.first_call > asof:
            out.append((bond.first_call, bond.call_price))
        else:  # callable at par on any coupon date once the first call has passed
            nxt = _next_coupon(bond.maturity, asof)
            if nxt < bond.maturity:
                out.append((nxt, 100.0))
    return out


def _next_coupon(maturity: date, asof: date) -> date:
    months = 0
    while add_months(maturity, -months - 6) > asof:
        months += 6
    return add_months(maturity, -months)


def price_at(
    coupon: float, maturity: date, asof: date, workout: date, redemption: float, y: float
) -> tuple[float, float, float]:
    """Return (clean price, accrued, modified duration) for yield ``y`` (decimal, semiannual).

    Cash flows run to ``workout`` on the maturity's coupon grid; 30/360 accrual; settlement is
    the pricing date.
    """
    nxt = _next_coupon(maturity, asof)
    previous = add_months(nxt, -6)
    accrued = coupon / 2.0 * days_360(previous, asof) / 180.0
    frac = days_360(asof, nxt) / 180.0
    periods, flows = [], []
    when, k = nxt, 0
    while when <= workout:
        last = when == workout
        periods.append(frac + k)
        flows.append(coupon / 2.0 + (redemption if last else 0.0))
        k += 1
        when = add_months(nxt, 6 * k)
    factor = 1.0 + y / 2.0
    pv = [cf / factor**p for cf, p in zip(flows, periods, strict=True)]
    dirty = math.fsum(pv)
    macaulay = math.fsum(p * v for p, v in zip(periods, pv, strict=True)) / dirty / 2.0
    return dirty - accrued, accrued, macaulay / factor


def price_bond(bond: Bond, asof: date, y: float) -> tuple[float, float, float]:
    """Return (clean, accrued, modified duration) at yield-to-worst ``y``: the lowest price."""
    best: tuple[float, float, float] | None = None
    for workout, redemption in _workouts(bond, asof):
        result = price_at(bond.coupon, bond.maturity, asof, workout, redemption, y)
        if best is None or result[0] < best[0]:
            best = result
    assert best is not None
    return best


class _Gauss:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def __call__(self, sigma: float = 1.0) -> float:
        return self.rng.gauss(0.0, sigma)


def _issuer_names(rng: random.Random, count: int) -> list[tuple[str, str, str]]:
    """Return (ticker, name, sector) for ``count`` fictional issuers, tickers unique."""
    words = sorted({w for w in FIRST}, key=lambda w: w)
    rng.shuffle(words)
    out: list[tuple[str, str, str]] = []
    seen = {"HALD", "MARL"}
    sectors = list(SECTORS)
    for word in words:
        ticker = word[:4].upper()
        if ticker in seen:
            continue
        seen.add(ticker)
        sector = sectors[len(out) % len(sectors)]
        out.append((ticker, f"{word} {rng.choice(SUFFIX[sector])}", sector))
        if len(out) == count:
            break
    return out


SCORE_BUCKETS = (
    (4, "AA"), (7, "A"), (10, "BBB"), (13, "BB"), (16, "B"), (21, "CCC"), (22, "D"),
)  # fmt: skip
BUCKET_NAMES = tuple(name for _, name in SCORE_BUCKETS)


def bucket_of(score: int) -> str:
    """Return the rating bucket (AA or better, A, BBB, BB, B, CCC or below, D) of a score."""
    for top, name in SCORE_BUCKETS:
        if score <= top:
            return name
    return "D"


def _rating_history(
    rng: random.Random, issuers: list[Issuer]
) -> tuple[list[tuple[str, str, str, str]], dict[str, list[int]]]:
    """Return S&P-style rating rows and month-end scores (2023-01 to 2025-12) per issuer.

    Before the window the path is random (rare notch changes, biased down below BBB); the
    in-window events are the planted ones. Moody's rows lag S&P by weeks and differ by a notch.
    """
    months = [date(2023 + (m // 12), m % 12 + 1, 1) for m in range(36)]
    rows: list[tuple[str, str, str, str]] = []
    panel: dict[str, list[int]] = {}
    for issuer in issuers:
        # Work backwards: the window starts at issuer.score; earlier months drift randomly.
        pre = [issuer.score]
        for _ in range(31):  # months 2023-01 .. 2025-07 end
            step = 0
            if rng.random() < 0.05:
                step = rng.choice((-1, 1)) if issuer.score <= 10 else rng.choice((-1, 1, 1))
            pre.append(max(1, min(21, pre[-1] + step)))
        path = list(reversed(pre))[:31]  # earliest first, ending at the window's start score
        scores = path[:]
        current = issuer.score
        event_dates = sorted(issuer.events)
        for month in range(31, 36):
            month_end = (months[month] + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            for when, new in event_dates:
                if when <= month_end:
                    current = new
            scores.append(current)
        panel[issuer.ticker] = scores
        last = None
        for month, score in enumerate(scores[:31]):  # the window's changes are the events
            if score != last:
                effective = months[month] if month else date(2022, 12, 15)
                rows.append((issuer.ticker, "SP", effective.isoformat(), SCALE[score - 1]))
                last = score
        for when, new in event_dates:
            rows.append((issuer.ticker, "SP", when.isoformat(), SCALE[new - 1]))
            moodys = max(1, new + rng.choice((-1, 0, 0, 1)))
            rows.append(
                (issuer.ticker, "MOODYS", (when + timedelta(days=9)).isoformat(), SCALE[moodys - 1])
            )
    rows = sorted(set(rows), key=lambda r: (r[0], r[2], r[1]))
    return rows, panel


def _rank_ic(signal: np.ndarray, forward: np.ndarray) -> list[float]:
    """Return each row's Spearman correlation over valid pairs (average ranks for ties)."""
    out: list[float] = []
    for s_row, r_row in zip(signal, forward, strict=True):
        valid = np.isfinite(s_row) & np.isfinite(r_row)
        if valid.sum() < 3:
            continue
        a, b = _avg_rank(s_row[valid]), _avg_rank(r_row[valid])
        a, b = a - a.mean(), b - b.mean()
        denom = math.sqrt(float((a * a).sum() * (b * b).sum()))
        if denom > 0:
            out.append(float((a * b).sum()) / denom)
    return out


def _avg_rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values))
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def build(root: Path, seed: int = DEFAULT_SEED, fmt: str = "parquet") -> dict[str, Any]:
    """Write the estate under ``root`` and return the known answers (also written to disk)."""
    rng = random.Random(seed)
    gauss = _Gauss(rng)
    days = business_days()
    first = days[0]
    if root.exists():
        shutil.rmtree(root)
    lake = root / "s3" / LAKE
    (root / "s3" / RESEARCH / "credit").mkdir(parents=True)
    (root / "rds").mkdir(parents=True)

    # ---- issuers ------------------------------------------------------------------------
    names = _issuer_names(rng, 78)
    issuers: list[Issuer] = []
    layout = [(4, 9)] * 28 + [(11, 13)] * 18 + [(14, 16)] * 22 + [(17, 19)] * 10
    for (ticker, name, sector), (lo, hi) in zip(names, layout, strict=True):
        score = rng.randint(lo, hi)
        issuers.append(
            Issuer(
                ticker,
                name,
                sector,
                score,
                0.010 if score <= 10 else 0.018,
                0.9 + 0.2 * rng.random(),
            )
        )
    halden = Issuer("HALD", "Halden Retail Group", "Consumer", 16, 0.02, 1.0)
    marlowe = Issuer("MARL", "Marlowe Offshore", "Energy", 15, 0.02, 1.0)
    halden.default_on, halden.default_ramp_from = date(2025, 9, 12), first
    marlowe.default_on, marlowe.default_ramp_from = date(2025, 11, 3), date(2025, 9, 1)
    halden.default_ramp, marlowe.default_ramp = 4.0, 3.5
    halden.events = [(date(2025, 8, 12), 18), (date(2025, 9, 2), 20), (date(2025, 9, 12), 22)]
    marlowe.events = [(date(2025, 9, 4), 16), (date(2025, 10, 14), 18), (date(2025, 11, 3), 22)]
    issuers += [halden, marlowe]
    by_ticker = {i.ticker: i for i in issuers}
    ordinary = [i for i in issuers if i.default_on is None]
    # Planted downgrades with a spread jump, and two upgrades.
    hy_names = [i for i in ordinary if 11 <= i.score <= 16]
    ig_names = [i for i in ordinary if i.score <= 10]
    planted_events: list[dict[str, Any]] = []
    for issuer, when, change in (
        (hy_names[0], date(2025, 9, 9), 1), (hy_names[3], date(2025, 10, 21), 2),
        (ig_names[1], date(2025, 11, 12), 2), (ig_names[5], date(2025, 12, 3), 3),
        (hy_names[7], date(2025, 10, 7), -1), (hy_names[9], date(2025, 11, 19), -1),
    ):  # fmt: skip
        new = issuer.score + change
        issuer.events.append((when, new))
        issuer.jumps[when] = 1.0 + 0.12 * change
        planted_events.append(
            {"issuer": issuer.ticker, "date": when.isoformat(), "from": SCALE[issuer.score - 1],
             "to": SCALE[new - 1]}
        )  # fmt: skip
    for issuer in ordinary:
        issuer.cds = False
    cds_pool = [i for i in ordinary if i.score <= 10][:24] + [i for i in ordinary if i.score > 10][
        :14
    ]
    for issuer in [*cds_pool, halden, marlowe]:
        issuer.cds = True
        issuer.basis = (
            round(rng.gauss(-8.0, 14.0), 1)
            if issuer.score <= 10
            else round(rng.gauss(10.0, 25.0), 1)
        )
    cds_pool[0].basis = -45.0  # planted: a clearly negative basis name

    # ---- bonds --------------------------------------------------------------------------
    bonds: list[Bond] = []

    def new_bond(
        issuer: Issuer, seniority: int, currency: str, recovery: float | None = None
    ) -> None:
        maturity = date(rng.randint(2027, 2035), rng.randint(1, 12), rng.choice((1, 15)))
        years = (maturity - first).days / 365.25
        curve = interpolate(CURVE_TENORS, BASE_CURVE[currency], years)
        oas0 = _start_spread(issuer.score) * SENIORITY_MULT[seniority]
        coupon = max(round((curve + oas0 / 100.0) * 8) / 8, 0.5)
        suffix = "AB"[sum(b.issuer == issuer.ticker for b in bonds) % 2]
        bond = Bond(
            f"{issuer.ticker}_{maturity.year % 100}{suffix}", issuer.ticker, currency, coupon,
            maturity, seniority, recovery=recovery,
        )  # fmt: skip
        if issuer.score > 10 and rng.random() < 0.4 and maturity.year >= 2030:
            bond.first_call = add_months(maturity, -36)
            bond.call_price = round(100.0 + coupon / 2.0, 2)
        bonds.append(bond)

    new_bond(halden, 1, "USD", 0.58)
    new_bond(halden, 2, "USD", 0.31)
    new_bond(marlowe, 2, "USD", 0.42)
    for issuer in ordinary:
        currency = "EUR" if rng.random() < 0.2 else "USD"
        new_bond(
            issuer, 2 if issuer.score <= 10 or rng.random() < 0.6 else rng.choice((1, 3)), currency
        )
    while len(bonds) < 150:
        issuer = rng.choice(ordinary)
        used = {b.bond_id for b in bonds}
        before = len(bonds)
        currency = next(b.currency for b in bonds if b.issuer == issuer.ticker)
        new_bond(issuer, rng.choice((1, 2, 3)), currency)
        if bonds[-1].bond_id in used:
            bonds.pop()
        assert len(bonds) >= before
    bonds.sort(key=lambda b: b.bond_id)
    bond_by_id = {b.bond_id: b for b in bonds}

    # ---- market simulation --------------------------------------------------------------
    n = len(days)
    m_ig = [0.0] + [gauss(0.011) for _ in range(n - 1)]
    m_hy = [0.0] + [0.7 * m_ig[i] + gauss(0.012) for i in range(1, n)]
    sector_f = {s: [0.0] + [gauss(0.005) for _ in range(n - 1)] for s in SECTORS}
    level = {c: [0.0] for c in BASE_CURVE}
    slope = {c: [0.0] for c in BASE_CURVE}
    for c in BASE_CURVE:
        for _ in range(n - 1):
            level[c].append(level[c][-1] + gauss(0.035))
            slope[c].append(slope[c][-1] + gauss(0.012))
    fx = [1.085]
    for _ in range(n - 1):
        fx.append(round(fx[-1] * math.exp(gauss(0.0035)), 5))

    def yield_curve(currency: str, i: int, years: float) -> float:
        base = interpolate(CURVE_TENORS, BASE_CURVE[currency], years)
        return base + level[currency][i] + slope[currency][i] * (math.log(max(years, 0.5)) - 1.5)

    spread: dict[str, list[float]] = {}
    idio: dict[str, float] = {}
    for issuer in issuers:
        path = [_start_spread(issuer.score) * math.exp(gauss(0.1))]
        idio[issuer.ticker] = 0.0
        spread[issuer.ticker] = path
    default_index = {
        i.ticker: days.index(i.default_on) for i in (halden, marlowe) if i.default_on is not None
    }
    for t in range(1, n):
        for issuer in issuers:
            path = spread[issuer.ticker]
            shock = gauss(issuer.sigma)
            idio[issuer.ticker] = 0.12 * idio[issuer.ticker] + shock  # the momentum seed
            market = m_ig[t] if issuer.score <= 10 else m_hy[t]
            move = issuer.beta * market + sector_f[issuer.sector][t] + idio[issuer.ticker]
            ramp = 0.0
            if issuer.default_on is not None and issuer.default_ramp_from is not None:
                start_i = days.index(issuer.default_ramp_from)
                if start_i <= t < days.index(issuer.default_on):
                    ramp = math.log(issuer.default_ramp) / (days.index(issuer.default_on) - start_i)
            jump = issuer.jumps.get(days[t], 1.0)
            path.append(path[-1] * math.exp(move + ramp) * jump)
    index_spread = {k: [v] for k, v in INDICES.items()}
    for t in range(1, n):
        for k in INDICES:
            market = m_ig[t] if k.endswith(("IG", "MAIN")) else m_hy[t]
            index_spread[k].append(index_spread[k][-1] * math.exp(0.9 * market + gauss(0.004)))
    basis_noise = {i.ticker: 0.0 for i in issuers if i.cds}

    # ---- daily bond table ---------------------------------------------------------------
    px: dict[str, list[dict[str, float | None]]] = {b.bond_id: [] for b in bonds}
    for t, day in enumerate(days):
        for bond in bonds:
            issuer = by_ticker[bond.issuer]
            default_at = default_index.get(bond.issuer)
            if default_at is not None and t >= default_at:
                price = round((bond.recovery or 0.4) * 100.0, 4)
                px[bond.bond_id].append(
                    {"clean": price, "accrued": 0.0, "ytw": None, "oas": None, "sdur": None,
                     "mdur": None, "ttm": round((bond.maturity - day).days / 365.25, 4)}
                )  # fmt: skip
                continue
            years = (bond.maturity - day).days / 365.25
            oas = spread[bond.issuer][t] * SENIORITY_MULT[bond.seniority] * (0.7 + 0.06 * years)
            oas = round(oas, 2)
            y = (yield_curve(bond.currency, t, years) + oas / 100.0) / 100.0
            clean, accrued, mdur = price_bond(bond, day, y)
            px[bond.bond_id].append(
                {"clean": round(clean, 4), "accrued": round(accrued, 4), "ytw": round(y * 100, 4),
                 "oas": oas, "sdur": round(mdur, 4), "mdur": round(mdur, 4),
                 "ttm": round(years, 4)}
            )  # fmt: skip
            del issuer
    tr: dict[str, list[float | None]] = {}
    for bond in bonds:
        rows = px[bond.bond_id]
        series: list[float | None] = [None]
        for t in range(1, n):
            prev, cur = rows[t - 1], rows[t]
            paid = bond.coupon / 2.0 if _coupon_paid(bond, days[t - 1], days[t]) else 0.0
            dirty0 = float(prev["clean"] or 0) + float(prev["accrued"] or 0)
            dirty1 = float(cur["clean"] or 0) + float(cur["accrued"] or 0)
            series.append(round((dirty1 + paid - dirty0) / dirty0, 8))
        tr[bond.bond_id] = series

    # ---- CDS and indices ----------------------------------------------------------------
    cds: list[dict[str, Any]] = []
    cds5: dict[str, list[float | None]] = {}
    for issuer in issuers:
        if not issuer.cds:
            continue
        cds5[issuer.ticker] = []
        for t in range(n):
            if issuer.ticker in default_index and t >= default_index[issuer.ticker]:
                cds5[issuer.ticker].append(None)
                continue
            basis_noise[issuer.ticker] = 0.8 * basis_noise[issuer.ticker] + gauss(2.5)
            value = spread[issuer.ticker][t] + issuer.basis + basis_noise[issuer.ticker]
            cds5[issuer.ticker].append(round(max(value, 5.0), 2))
    for t, day in enumerate(days):
        for ticker, series5 in cds5.items():
            s5 = series5[t]
            if s5 is None:
                continue
            for tenor, factor in (("3Y", 0.8), ("5Y", 1.0), ("10Y", 1.22)):
                cds.append(
                    {"dt": day, "name_id": ticker, "kind": "single", "tenor": tenor,
                     "spread_bp": round(s5 * factor, 2), "recovery": CDS_RECOVERY}
                )  # fmt: skip
        for k, path in index_spread.items():
            cds.append(
                {"dt": day, "name_id": k, "kind": "index", "tenor": "5Y",
                 "spread_bp": round(path[t], 2), "recovery": CDS_RECOVERY}
            )  # fmt: skip

    rates: list[dict[str, Any]] = []
    for t, day in enumerate(days):
        for currency, label in (("USD", "UST"), ("EUR", "EURGOV")):
            for years in CURVE_TENORS:
                rates.append(
                    {"dt": day, "series_id": f"{label}_{int(years)}Y",
                     "value": round(yield_curve(currency, t, years), 4)}
                )  # fmt: skip
        rates.append({"dt": day, "series_id": "EURUSD", "value": fx[t]})

    # ---- positions, trades, financing ---------------------------------------------------
    books = _assign_books(rng, bonds, by_ticker)
    positions: dict[tuple[str, str], list[float]] = {}
    trades: list[tuple[Any, ...]] = []
    for bond_id, (book, sign, size) in books.items():
        bond_by_id[bond_id].book = book
        face = sign * size
        faces = [face] * n
        trades.append((len(trades) + 1, first.isoformat(), book, "bond", bond_id,
                       "BUY" if sign > 0 else "SELL", size, px[bond_id][0]["clean"]))  # fmt: skip
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            t = rng.randint(5, n - 5)
            if (
                bond_by_id[bond_id].issuer in default_index
                and t >= default_index[bond_by_id[bond_id].issuer] - 3
            ):
                continue
            delta = sign * round(size * rng.choice((-0.3, -0.2, 0.2, 0.3)), -5)
            for u in range(t, n):
                faces[u] += delta
            trades.append((len(trades) + 1, days[t].isoformat(), book, "bond", bond_id,
                           "BUY" if delta > 0 else "SELL", abs(delta), px[bond_id][t]["clean"]))  # fmt: skip
        positions[book, bond_id] = faces

    def mv_usd(bond_id: str, t: int, face: float) -> float:
        bond = bond_by_id[bond_id]
        row = px[bond_id][t]
        rate = fx[t] if bond.currency == "EUR" else 1.0
        return round(
            face * (float(row["clean"] or 0) + float(row["accrued"] or 0)) / 100.0 * rate, 2
        )

    pos_rows: list[tuple[Any, ...]] = []
    mv: dict[tuple[str, str], list[float]] = {}
    for (book, bond_id), faces in positions.items():
        values = [mv_usd(bond_id, t, faces[t]) for t in range(n)]
        mv[book, bond_id] = values
        for t in range(n):
            pos_rows.append((days[t].isoformat(), book, bond_id, faces[t], values[t]))
    cds_pos: list[tuple[Any, ...]] = []
    cds_notional: dict[str, float] = {}
    for (book, bond_id), faces in positions.items():
        bond = bond_by_id[bond_id]
        fresh = bond.issuer not in cds_notional
        if book == "ig_rv" and by_ticker[bond.issuer].cds and fresh and faces[0] > 0:
            rate = fx[0] if bond.currency == "EUR" else 1.0
            cds_notional[bond.issuer] = round(0.9 * faces[0] * rate, -5)
    for ticker, notional in sorted(cds_notional.items()):
        trades.append((len(trades) + 1, first.isoformat(), "ig_rv", "cds", ticker, "BUY_PROTECTION",
                       notional, None))  # fmt: skip
        for t in range(n):
            cds_pos.append((days[t].isoformat(), "ig_rv", ticker, "5Y", notional))
    index_notional = {"CDXLIKE_HY": 150e6, "ITXLIKE_XOVER": 50e6, "CDXLIKE_IG": -200e6}
    idx_pos: list[tuple[Any, ...]] = []
    for k, notional in index_notional.items():
        trades.append((len(trades) + 1, first.isoformat(), "overlay", "index", k,
                       "BUY_PROTECTION" if notional > 0 else "SELL_PROTECTION", abs(notional), None))  # fmt: skip
        for t in range(n):
            idx_pos.append((days[t].isoformat(), "overlay", k, notional))
    repo_rates = [round(4.30 + 0.02 * math.sin(t / 7.0), 4) for t in range(n)]
    fees: dict[str, float] = {}
    for (_book, bond_id), faces in positions.items():
        if faces[0] < 0:
            special = rng.random() < 0.25
            fees[bond_id] = round(rng.uniform(150, 450) if special else rng.uniform(25, 90), 1)
    haircut, repo_spread_bp = 0.10, 35.0

    pnl: dict[str, list[tuple[float, float, float]]] = {b: [(0.0, 0.0, 0.0)] for b in BOOKS}
    for t in range(1, n):
        gap = (days[t] - days[t - 1]).days
        for book in BOOKS:
            gross = financing = 0.0
            for (b_book, bond_id), held in mv.items():
                if b_book != book:
                    continue
                gross += held[t - 1] * float(tr[bond_id][t] or 0.0)
                if held[t - 1] > 0:
                    rate = (repo_rates[t - 1] + repo_spread_bp / 100.0) / 100.0
                    financing += held[t - 1] * (1 - haircut) * rate * gap / 360.0
                else:
                    financing += -held[t - 1] * fees[bond_id] / 1e4 * gap / 360.0
            if book == "ig_rv":
                for ticker, notional in cds_notional.items():
                    s0, s1 = cds5[ticker][t - 1], cds5[ticker][t]
                    if s0 is None or s1 is None:
                        continue
                    gross += notional * risky_annuity(s0) * (s1 - s0) / 1e4
                    gross -= notional * s0 / 1e4 * gap / 360.0
            if book == "overlay":
                for k, notional in index_notional.items():
                    s0, s1 = index_spread[k][t - 1], index_spread[k][t]
                    gross += notional * risky_annuity(s0) * (s1 - s0) / 1e4
                    gross -= notional * s0 / 1e4 * gap / 360.0
            pnl[book].append((round(gross, 2), round(financing, 2), round(gross - financing, 2)))
    returns = {
        book: [
            None if t == 0 else pnl[book][t][2] / CAPITAL.get(book, FUND_CAPITAL) for t in range(n)
        ]
        for book in BOOKS
    }

    # ---- ratings and risk model ---------------------------------------------------------
    rating_rows, rating_panel = _rating_history(rng, issuers)
    factor_series = {
        "rates_usd": [0.0] + [(level["USD"][t] - level["USD"][t - 1]) / 100.0 for t in range(1, n)],
        "rates_eur": [0.0] + [(level["EUR"][t] - level["EUR"][t - 1]) / 100.0 for t in range(1, n)],
        "credit_ig": m_ig,
        "credit_hy": m_hy,
    }
    fm_rows, cov_rows, specific_rows = _risk_model(bonds, px, by_ticker, factor_series, days)

    # ---- signal -------------------------------------------------------------------------
    signal: list[list[float | None]] = []  # days x bonds
    for t in range(n):
        row: list[float | None] = []
        for bond in bonds:
            now, back = (
                px[bond.bond_id][t]["oas"],
                px[bond.bond_id][t - 20]["oas"] if t >= 20 else None,
            )
            row.append(None if now is None or back is None else round(-(now / back - 1.0), 6))
        signal.append(row)

    # ---- write -------------------------------------------------------------------------
    _write_lake(lake, days, bonds, px, tr, cds, rates, signal, rating_rows, issuers, fm_rows,
                cov_rows, specific_rows, fmt, rng)  # fmt: skip
    oms = root / "rds" / "wrenfield_oms.sqlite"
    _write_oms(oms, days, bonds, issuers, pos_rows, cds_pos, idx_pos, trades, repo_rates, fees,
               pnl, returns, books)  # fmt: skip
    (root / "uri_map.json").write_text(
        json.dumps(
            {
                f"s3://{LAKE}": f"s3/{LAKE}",
                f"s3://{RESEARCH}": f"s3/{RESEARCH}",
                OMS_URI: "rds/wrenfield_oms.sqlite",
            },
            indent=2,
        )
        + "\n",
        "utf-8",
    )

    for doc in sorted((Path(__file__).resolve().parents[1] / "research").glob("*.md")):
        shutil.copy(doc, root / "s3" / RESEARCH / "credit" / doc.name)  # the research bucket

    # ---- known answers ------------------------------------------------------------------
    arr_signal = np.array([[np.nan if v is None else v for v in row] for row in signal])
    arr_tr = np.array([[np.nan if v is None else v for v in tr[b.bond_id]] for b in bonds]).T
    ic0 = _rank_ic(arr_signal[:-1], arr_tr[1:])
    ic1 = _rank_ic(arr_signal[:-2], arr_tr[2:])
    answers = _answers(
        seed, days, bonds, px, mv, books, bond_by_id, by_ticker, cds5, cds_notional, ic0, ic1,
        planted_events, rating_panel, tr, returns, pnl, fx, index_spread, index_notional,
    )  # fmt: skip
    (root / "known_answers.json").write_text(json.dumps(answers, indent=2) + "\n", "utf-8")
    return answers


def _start_spread(score: int) -> float:
    table = {4: 55, 5: 65, 6: 80, 7: 95, 8: 120, 9: 150, 10: 190, 11: 260, 12: 320, 13: 400,
             14: 480, 15: 580, 16: 750, 17: 1000, 18: 1250, 19: 1600}  # fmt: skip
    return float(table.get(score, 130 if score < 4 else 2000))


def _coupon_paid(bond: Bond, before: date, today: date) -> bool:
    return _next_coupon(bond.maturity, before) <= today


def _assign_books(
    rng: random.Random, bonds: list[Bond], by_ticker: dict[str, Issuer]
) -> dict[str, tuple[str, int, float]]:
    """Return {bond_id: (book, +1 long or -1 short, face in millions of local units)}."""
    out: dict[str, tuple[str, int, float]] = {}
    hy = [b for b in bonds if 11 <= by_ticker[b.issuer].score <= 16 and b.issuer != "HALD"]
    ig = [b for b in bonds if by_ticker[b.issuer].score <= 10 and by_ticker[b.issuer].cds]
    dist = [b for b in bonds if by_ticker[b.issuer].score >= 17 or b.issuer == "HALD"]
    rng.shuffle(hy)
    rng.shuffle(ig)
    for b in [x for x in hy if x.issuer == "MARL"] + [x for x in hy if x.issuer != "MARL"][:44]:
        out[b.bond_id] = ("hy_ls", 1, rng.choice((4, 5, 6, 8, 10)) * 1e6)
    shorts = [x for x in hy if x.bond_id not in out][:12]
    for b in shorts:
        out[b.bond_id] = ("hy_ls", -1, rng.choice((3, 4, 5)) * 1e6)
    for b in ig[:30]:
        out[b.bond_id] = ("ig_rv", 1, rng.choice((10, 15, 20)) * 1e6)
    for b in dist[:15]:
        out[b.bond_id] = ("distressed", 1, rng.choice((3, 5, 8)) * 1e6)
    return out


def _risk_model(
    bonds: list[Bond],
    px: dict[str, list[dict[str, float | None]]],
    by_ticker: dict[str, Issuer],
    factors: dict[str, list[float]],
    days: list[date],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Return the vendor-style factor model at the last date: loadings, covariance, specific."""
    as_of = days[-1].isoformat()
    names = list(factors)
    loadings: list[tuple[Any, ...]] = []
    specific: list[tuple[Any, ...]] = []
    for bond in bonds:
        row = px[bond.bond_id][-1]
        if row["mdur"] is None or row["oas"] is None:
            continue
        dts = float(row["sdur"] or 0) * float(row["oas"])
        issuer = by_ticker[bond.issuer]
        rates = "rates_usd" if bond.currency == "USD" else "rates_eur"
        credit = "credit_ig" if issuer.score <= 10 else "credit_hy"
        loadings.append((as_of, bond.bond_id, rates, round(-float(row["mdur"]), 6)))
        loadings.append((as_of, bond.bond_id, credit, round(-dts / 1e4, 6)))
        specific.append((as_of, bond.bond_id, round((issuer.sigma * dts / 1e4) ** 2 * 252, 10)))
    cov: list[tuple[Any, ...]] = []
    for a in names:
        for b in names:
            xs, ys = factors[a][1:], factors[b][1:]
            mx, my = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
            value = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
            cov.append((as_of, a, b, round(value / (len(xs) - 1) * 252, 10)))
    return loadings, cov, specific


def _answers(
    seed: int, days: list[date], bonds: list[Bond], px: dict[str, list[dict[str, float | None]]],
    mv: dict[tuple[str, str], list[float]], books: dict[str, tuple[str, int, float]],
    bond_by_id: dict[str, Bond], by_ticker: dict[str, Issuer], cds5: dict[str, list[float | None]],
    cds_notional: dict[str, float], ic0: list[float], ic1: list[float],
    planted_events: list[dict[str, Any]], rating_panel: dict[str, list[int]],
    tr: dict[str, list[float | None]], returns: dict[str, list[float | None]],
    pnl: dict[str, list[tuple[float, float, float]]], fx: list[float],
    index_spread: dict[str, list[float]], index_notional: dict[str, float],
) -> dict[str, Any]:  # fmt: skip
    n = len(days)
    last = n - 1
    dts: dict[str, float] = {}
    for book, capital in (*CAPITAL.items(), ("fund", FUND_CAPITAL)):
        terms = []
        for (b_book, bond_id), series in mv.items():
            row = px[bond_id][last]
            if (book == "fund" or b_book == book) and row["oas"] is not None:
                terms.append(series[last] / capital * float(row["sdur"] or 0) * float(row["oas"]))
        dts[book] = round(math.fsum(terms), 6)
    pre = days.index(date(2025, 9, 5))
    jtd: dict[str, float] = {}
    for (_b_book, bond_id), series in mv.items():
        bond = bond_by_id[bond_id]
        if bond.issuer == "HALD":
            recovery = RECOVERY_ASSUMED[bond.seniority]
            face_usd = series[pre] / (
                (float(px[bond_id][pre]["clean"] or 0) + float(px[bond_id][pre]["accrued"] or 0))
                / 100.0
            )
            jtd[bond_id] = round(face_usd * recovery - series[pre], 2)
    basis_date = days.index(date(2025, 12, 15))
    basis: dict[str, float] = {}
    ttm_tenors = (3.0, 5.0, 10.0)
    for bond in bonds:
        s5 = cds5.get(bond.issuer, [None] * n)[basis_date]
        oas = px[bond.bond_id][basis_date]["oas"]
        if s5 is None or oas is None:
            continue
        spreads = (round(s5 * 0.8, 2), s5, round(s5 * 1.22, 2))  # as stored
        cds_at = interpolate(ttm_tenors, spreads, float(px[bond.bond_id][basis_date]["ttm"] or 0))
        basis[bond.bond_id] = round(cds_at - float(oas), 4)
    migration: dict[str, dict[str, int]] = {a: dict.fromkeys(BUCKET_NAMES, 0) for a in BUCKET_NAMES}
    for scores in rating_panel.values():
        for m in range(len(scores) - 12):
            migration[bucket_of(scores[m])][bucket_of(scores[m + 12])] += 1
    return {
        "seed": seed,
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
        "business_days": n,
        "bonds": len(bonds),
        "issuers": len(by_ticker),
        "defaults": {
            "HALD": {
                "date": "2025-09-12",
                "recoveries": {b.bond_id: b.recovery for b in bonds if b.issuer == "HALD"},
            },
            "MARL": {
                "date": "2025-11-03",
                "recoveries": {b.bond_id: b.recovery for b in bonds if b.issuer == "MARL"},
            },
            "convention": "recovery is the post-default price as a fraction of face; the bond trades at it from the default date, with no accrued",
        },
        "dts": {
            "date": days[last].isoformat(),
            "values": dts,
            "convention": "sum over bonds of (market value USD / book capital) x spread duration x OAS in bp; bonds with no OAS (defaulted) are left out; fund capital is the sum of the three books' capital",
        },
        "jump_to_default_halden": {
            "date": "2025-09-05",
            "usd_by_bond": jtd,
            "convention": "face x assumed recovery - market value, with assumed recovery 0.60 secured, 0.40 senior unsecured, 0.15 subordinated; positive is a gain",
        },
        "basis": {
            "date": "2025-12-15",
            "bp_by_bond": basis,
            "convention": "CDS spread at the bond's years to maturity (linear in 3Y, 5Y, 10Y; flat outside) minus OAS; negative: the bond is cheap to the CDS",
        },
        "spread_momentum": {
            "convention": "signal = -(OAS_t / OAS_{t-20} - 1); rank IC is the mean over days of the cross-sectional Spearman correlation (average ranks, NaN pairs dropped, days with fewer than 3 pairs skipped) between the signal on day t and the bond's total return on day t+1+lag",
            "lag0": {"mean_rank_ic": float(np.mean(ic0)), "days": len(ic0)},
            "lag1": {"mean_rank_ic": float(np.mean(ic1)), "days": len(ic1)},
        },
        "rating_events": planted_events,
        "migration_12m_counts": migration,
        "migration_convention": "month-end S&P scores, every 12-month window, bucket AA (AAA to AA-), A, BBB, BB, B, CCC (CCC+ to C), D",
        "ig_cds_notional_usd": cds_notional,
        "book_net_pnl_total": {b: round(math.fsum(p[2] for p in pnl[b]), 2) for b in BOOKS},
        "book_financing_total": {b: round(math.fsum(p[1] for p in pnl[b]), 2) for b in BOOKS},
    }


def _write_table(
    lake: Path, table: str, partition: str, rows: list[dict[str, Any]], fmt: str
) -> None:
    folder = lake / "curated" / table / partition
    folder.mkdir(parents=True, exist_ok=True)
    if fmt == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq

        # An all-null column would be written as type null and break a read across partitions
        # (found by DuckDB on the first partition's total_return); the curation job pins doubles.
        columns = {
            k: pa.array(v, type=pa.float64() if all(x is None for x in v) else None)
            for k in rows[0]
            for v in [[r[k] for r in rows]]
        }
        pq.write_table(
            pa.table(columns), folder / "part-0000.parquet", compression="zstd", compression_level=9
        )
    else:
        with (folder / "part-0000.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def _write_lake(
    lake: Path, days: list[date], bonds: list[Bond], px: dict[str, list[dict[str, float | None]]],
    tr: dict[str, list[float | None]], cds: list[dict[str, Any]], rates: list[dict[str, Any]],
    signal: list[list[float | None]], rating_rows: list[tuple[str, str, str, str]],
    issuers: list[Issuer], fm_rows: list[tuple[Any, ...]], cov_rows: list[tuple[Any, ...]],
    specific_rows: list[tuple[Any, ...]], fmt: str, rng: random.Random,
) -> None:  # fmt: skip
    cds_by_day: dict[date, list[dict[str, Any]]] = {}
    for row in cds:
        cds_by_day.setdefault(row["dt"], []).append({k: v for k, v in row.items() if k != "dt"})
    rates_by_day: dict[date, list[dict[str, Any]]] = {}
    for row in rates:
        rates_by_day.setdefault(row["dt"], []).append({k: v for k, v in row.items() if k != "dt"})
    for t, day in enumerate(days):
        rows = [
            {"bond_id": b.bond_id, "currency": b.currency, "clean_price": px[b.bond_id][t]["clean"],
             "accrued_interest": px[b.bond_id][t]["accrued"], "ytw_pct": px[b.bond_id][t]["ytw"],
             "oas_bp": px[b.bond_id][t]["oas"], "spread_duration": px[b.bond_id][t]["sdur"],
             "modified_duration": px[b.bond_id][t]["mdur"], "years_to_maturity": px[b.bond_id][t]["ttm"],
             "total_return": tr[b.bond_id][t]}
            for b in bonds
        ]  # fmt: skip
        key = f"dt={day.isoformat()}"
        _write_table(lake, "bond_px_daily", key, rows, fmt)
        _write_table(lake, "cds_daily", key, cds_by_day[day], fmt)
        _write_table(lake, "rates_daily", key, rates_by_day[day], fmt)
    months = sorted({d.strftime("%Y-%m") for d in days})
    for month in months:
        rows = [
            {"dt": days[t].isoformat(), "bond_id": b.bond_id, "value": signal[t][i]}
            for t in range(len(days)) if days[t].strftime("%Y-%m") == month
            for i, b in enumerate(bonds)
        ]  # fmt: skip
        _write_table(lake, "sig_spread_momentum", f"month={month}", rows, fmt)
    snapshot = f"dt={days[-1].isoformat()}"
    _write_table(
        lake, "ref_bond", snapshot,
        [{"bond_id": b.bond_id, "issuer_id": b.issuer, "currency": b.currency, "coupon_pct": b.coupon,
          "maturity": b.maturity.isoformat(), "seniority": b.seniority,
          "first_call": b.first_call.isoformat() if b.first_call else None, "call_price": b.call_price}
         for b in bonds],
        fmt,
    )  # fmt: skip
    _write_table(
        lake, "ref_issuer", snapshot,
        [{"issuer_id": i.ticker, "name": i.name, "sector": i.sector, "sector_code": SECTORS[i.sector],
          "has_cds": i.cds} for i in issuers],
        fmt,
    )  # fmt: skip
    _write_table(
        lake, "rating_history", snapshot,
        [{"issuer_id": r[0], "agency": r[1], "effective_date": r[2], "rating": r[3]} for r in rating_rows],
        fmt,
    )  # fmt: skip
    _write_table(lake, "fm_loadings", snapshot,
                 [{"as_of": r[0], "bond_id": r[1], "factor_id": r[2], "loading": r[3]} for r in fm_rows], fmt)  # fmt: skip
    _write_table(lake, "fm_factor_cov", snapshot,
                 [{"as_of": r[0], "factor_id_1": r[1], "factor_id_2": r[2], "cov_annual": r[3]} for r in cov_rows], fmt)  # fmt: skip
    _write_table(lake, "fm_specific", snapshot,
                 [{"as_of": r[0], "bond_id": r[1], "specific_var_annual": r[2]} for r in specific_rows], fmt)  # fmt: skip

    # One vendor file, as it landed in raw/ (the curated tables are made from files like it).
    last = len(days) - 1
    raw = lake / "raw" / "vendor=quotesource_like" / f"dt={days[-1].isoformat()}"
    raw.mkdir(parents=True)
    with (raw / "bond_prices_20251231.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            ["CUSIP_ID", "Px Clean", "Accrued Int", "YTW %", "OAS (bp)", "Spread Dur", "Mod Dur"]
        )
        for b in bonds:
            row = px[b.bond_id][last]
            writer.writerow([b.bond_id, row["clean"], row["accrued"], row["ytw"], row["oas"], row["sdur"], row["mdur"]])  # fmt: skip
    (lake / "catalog").mkdir()
    (lake / "athena").mkdir()
    (lake / "catalog" / "wrenfield_curated.json").write_text(
        json.dumps(_glue_catalog(fmt), indent=2) + "\n", "utf-8"
    )
    (lake / "athena" / "views.sql").write_text(ATHENA_VIEWS, "utf-8")
    del rng


def _glue_catalog(fmt: str) -> dict[str, Any]:
    tables = {
        "bond_px_daily": [
            "bond_id string",
            "currency string",
            "clean_price double",
            "accrued_interest double",
            "ytw_pct double",
            "oas_bp double",
            "spread_duration double",
            "modified_duration double",
            "years_to_maturity double",
            "total_return double",
        ],
        "cds_daily": [
            "name_id string",
            "kind string",
            "tenor string",
            "spread_bp double",
            "recovery double",
        ],
        "rates_daily": ["series_id string", "value double"],
        "sig_spread_momentum": ["dt string", "bond_id string", "value double"],
    }
    keys = {"sig_spread_momentum": "month"}
    return {
        "DatabaseList": [{"Name": "wrenfield_curated", "LocationUri": f"s3://{LAKE}/curated/"}],
        "TableList": [
            {
                "Name": name,
                "DatabaseName": "wrenfield_curated",
                "StorageDescriptor": {
                    "Columns": [dict(zip(("Name", "Type"), c.split(), strict=True)) for c in cols],
                    "Location": f"s3://{LAKE}/curated/{name}/",
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
                    if fmt == "parquet"
                    else "org.apache.hadoop.mapred.TextInputFormat",
                },
                "PartitionKeys": [{"Name": keys.get(name, "dt"), "Type": "string"}],
                "Parameters": {"classification": fmt, "partition_filtering.enabled": "true"},
            }
            for name, cols in tables.items()
        ],
    }


ATHENA_VIEWS = """\
-- Athena views over the curated tables (the fund's analysts query these in the console).
CREATE OR REPLACE VIEW wrenfield_curated.v_bond_px_latest AS
SELECT * FROM wrenfield_curated.bond_px_daily
WHERE dt = (SELECT max(dt) FROM wrenfield_curated.bond_px_daily);

CREATE OR REPLACE VIEW wrenfield_curated.v_cds_5y AS
SELECT dt, name_id, spread_bp FROM wrenfield_curated.cds_daily
WHERE tenor = '5Y' AND kind = 'single';
"""


OMS_SCHEMA = """
CREATE TABLE ref_books (book TEXT PRIMARY KEY, pm TEXT NOT NULL, capital_usd REAL);
CREATE TABLE ref_issuer_master (issuer_id TEXT PRIMARY KEY, name TEXT NOT NULL, sector TEXT NOT NULL,
  has_cds INTEGER NOT NULL, book TEXT);
CREATE TABLE pos_eod (date TEXT NOT NULL, book TEXT NOT NULL, bond_id TEXT NOT NULL,
  face REAL NOT NULL, mv_usd REAL NOT NULL, PRIMARY KEY (date, book, bond_id));
CREATE TABLE pos_cds (date TEXT NOT NULL, book TEXT NOT NULL, name_id TEXT NOT NULL,
  tenor TEXT NOT NULL, protection_notional_usd REAL NOT NULL, PRIMARY KEY (date, book, name_id, tenor));
CREATE TABLE pos_index (date TEXT NOT NULL, book TEXT NOT NULL, index_id TEXT NOT NULL,
  protection_notional_usd REAL NOT NULL, PRIMARY KEY (date, book, index_id));
CREATE TABLE trd_trades (trade_id INTEGER PRIMARY KEY, trade_date TEXT NOT NULL, book TEXT NOT NULL,
  instrument_type TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL,
  quantity REAL NOT NULL, price REAL);
CREATE TABLE fin_repo_rates (date TEXT PRIMARY KEY, rate_pct REAL NOT NULL);
CREATE TABLE fin_borrow_fees (bond_id TEXT PRIMARY KEY, fee_bp REAL NOT NULL);
CREATE TABLE pnl_daily (date TEXT NOT NULL, book TEXT NOT NULL, gross_pnl_usd REAL NOT NULL,
  financing_cost_usd REAL NOT NULL, net_pnl_usd REAL NOT NULL, PRIMARY KEY (date, book));
CREATE TABLE cmp_restricted (issuer_id TEXT PRIMARY KEY, status TEXT NOT NULL, since TEXT NOT NULL);
"""


def _write_oms(
    path: Path, days: list[date], bonds: list[Bond], issuers: list[Issuer],
    pos_rows: list[tuple[Any, ...]], cds_pos: list[tuple[Any, ...]], idx_pos: list[tuple[Any, ...]],
    trades: list[tuple[Any, ...]], repo_rates: list[float], fees: dict[str, float],
    pnl: dict[str, list[tuple[float, float, float]]], returns: dict[str, list[float | None]],
    books: dict[str, tuple[str, int, float]],
) -> None:  # fmt: skip
    del returns
    pm = {
        "hy_ls": "pm-hy",
        "ig_rv": "pm-ig",
        "distressed": "pm-distressed",
        "overlay": "pm-overlay",
    }
    book_of_issuer = {b.issuer: books[b.bond_id][0] for b in bonds if b.bond_id in books}
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(OMS_SCHEMA)
        db.executemany(
            "INSERT INTO ref_books VALUES (?,?,?)", [(b, pm[b], CAPITAL.get(b)) for b in BOOKS]
        )
        db.executemany(
            "INSERT INTO ref_issuer_master VALUES (?,?,?,?,?)",
            [
                (i.ticker, i.name, i.sector, int(i.cds), book_of_issuer.get(i.ticker))
                for i in issuers
            ],
        )
        db.executemany("INSERT INTO pos_eod VALUES (?,?,?,?,?)", pos_rows)
        db.executemany("INSERT INTO pos_cds VALUES (?,?,?,?,?)", cds_pos)
        db.executemany("INSERT INTO pos_index VALUES (?,?,?,?)", idx_pos)
        db.executemany("INSERT INTO trd_trades VALUES (?,?,?,?,?,?,?,?)", trades)
        db.executemany("INSERT INTO fin_repo_rates VALUES (?,?)", [(d.isoformat(), r) for d, r in zip(days, repo_rates, strict=True)])  # fmt: skip
        db.executemany("INSERT INTO fin_borrow_fees VALUES (?,?)", sorted(fees.items()))
        for book in BOOKS:
            db.executemany(
                "INSERT INTO pnl_daily VALUES (?,?,?,?,?)",
                [(days[t].isoformat(), book, *pnl[book][t]) for t in range(len(days))],
            )
        restricted = [i for i in issuers if i.cds][2:3] + [i for i in issuers if not i.cds][:1]
        db.executemany(
            "INSERT INTO cmp_restricted VALUES (?,?,?)",
            [
                (restricted[0].ticker, "restricted", "2025-07-01"),
                (restricted[1].ticker, "wall_crossed", "2025-10-15"),
            ],
        )
    with closing(sqlite3.connect(path)) as db:
        db.execute("VACUUM")


def main() -> None:
    """Build the estate at ROOT (default ./wrenfield-estate)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("wrenfield-estate"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    args = parser.parse_args()
    answers = build(args.root, args.seed, args.format)
    print(f"built {args.root}: {answers['bonds']} bonds, {answers['business_days']} days")


if __name__ == "__main__":
    main()
