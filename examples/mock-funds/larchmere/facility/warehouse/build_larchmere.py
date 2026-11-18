"""Build Larchmere Loan Partners' data estate: a deterministic, fictional CLO manager's data.

    python build_larchmere.py [OUT_DIR] [--seed N]

Larchmere runs on Azure. This builds local stand-ins that keep the real shapes (everything under
OUT_DIR; all names and numbers are fictional):

- ``sql/clo.db``: the Azure SQL database (schema ``clo``), modelled with SQLite. The T-SQL it
  stands for is ``facility/azure_sql/schema.sql`` and ``views.sql``. SQLite has no schemas, so
  the store attaches the file as ``clo``; dialect gaps are listed in ``azure_sql/README.md``.
- ``lake/raw``, ``lake/curated``: the ADLS Gen2 containers ``raw`` and ``curated`` of the storage
  account ``larchmereadls``. Raw holds the trustee's CSV drops; curated holds Parquet, one folder
  per table, hive-partitioned (``dt=YYYY-MM-DD``). Without pyarrow the curated files are CSV with
  the same layout.
- ``lake/docs``: the SharePoint library "CLO Research", exported to the Blob container ``research``.
- ``lake/catalog.json``: which ``abfss://`` URI is which directory, and the tables' columns.
- ``known_answers.json``: the truths planted in the data, with their conventions.

Only the standard library is needed (pyarrow, when present, writes Parquet).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import zipfile
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SEED = 20261118
KNOWN_ANSWERS = "known_answers.json"
ACCOUNT = "larchmereadls"
FIRST, LAST = date(2023, 10, 31), date(2026, 10, 30)  # trustee report dates, month-end
DAILY_FROM = date(2025, 5, 1)  # the pricing vendor's feed starts here; earlier marks are monthly
TRANCHES = ("AAA", "AA", "A", "BBB", "BB", "EQ")
DEBT = TRANCHES[:-1]
OC_CLASSES = {"oc_ab": ("AAA", "AA"), "oc_c": ("AAA", "AA", "A"), "oc_d": DEBT[:4], "oc_e": DEBT}
IC_CLASSES = {"ic_ab": OC_CLASSES["oc_ab"], "ic_c": OC_CLASSES["oc_c"],
              "ic_d": OC_CLASSES["oc_d"], "ic_e": DEBT}  # fmt: skip
MIN_TESTS = ("oc_ab", "oc_c", "oc_d", "oc_e", "ic_ab", "ic_c", "ic_d", "ic_e", "was", "diversity")
MAX_TESTS = ("ccc_pct", "warf", "wal", "obligor_pct", "industry_pct", "second_lien_pct", "bond_pct")
DEFAULT_RF = 10000
CCC_RF = 4770
# Moody's published rating factors (the scale is public; the ratings below are fictional).
RATING_FACTOR = {
    "Ba1": 940, "Ba2": 1350, "Ba3": 1766, "B1": 2220, "B2": 2720, "B3": 3490,
    "Caa1": 4770, "Caa2": 6500, "Caa3": 8070, "Ca": 10000, "D": 10000,
}  # fmt: skip
LADDER = ["Ba1", "Ba2", "Ba3", "B1", "B2", "B3", "Caa1", "Caa2"]
DS_TABLE = [0.0, 1.0, 1.5, 2.0, 2.33, 2.67, 3.0, 3.25, 3.5, 3.75, 4.0]
INDUSTRIES = [
    "Aerospace and Defense", "Automotive Parts", "Banking Software", "Broadcasting", "Business Services",
    "Chemicals", "Construction Materials", "Consumer Packaging", "Containers and Glass", "Education Services",
    "Electronics", "Food Distribution", "Gaming and Lodging", "Healthcare Providers", "Healthcare Technology",
    "Industrial Machinery", "Insurance Brokerage", "IT Consulting", "Leisure Products", "Media Publishing",
    "Packaging Films", "Restaurants", "Specialty Retail", "Telecom Towers", "Waste Services",
]  # fmt: skip
WORDS_A = ["Alder", "Brightwater", "Cobalt", "Dunmore", "Elmstead", "Fenwick", "Gannet", "Harrow",
           "Ironbark", "Juniper", "Kestrel", "Lindqvist", "Marlowe", "Northgate", "Orchard", "Pemberly",
           "Quillon", "Redfern", "Stonebridge", "Thornfield", "Umber", "Vantage", "Westmoor", "Yarrow",
           "Zephyr", "Ashdown", "Bellweather", "Corvane", "Deepwell", "Eastlake"]  # fmt: skip
WORDS_B = ["Holdings", "Logistics", "Systems", "Brands", "Health", "Labs", "Networks", "Industries",
           "Partners", "Group"]  # fmt: skip
DEALS = {
    "LARCH-2021-1": {
        "name": "Larchmere CLO 2021-1", "status": "REINVESTING", "closed": "2021-06-17",
        "reinvest_end": "2026-07-20",
        "balances": {"AAA": 310e6, "AA": 55e6, "A": 30e6, "BBB": 30e6, "BB": 25e6, "EQ": 50e6},
        "oc": {"oc_ab": 1.335, "oc_c": 1.235, "oc_d": 1.145, "oc_e": 1.085},
        "ic": {"ic_ab": 1.20, "ic_c": 1.15, "ic_d": 1.10, "ic_e": 1.05},
        "limits": {"ccc_pct": 7.5, "warf": 3300.0, "was": 3.40, "wal": 8.0, "diversity": 48.0,
                   "obligor_pct": 3.5, "industry_pct": 15.0, "second_lien_pct": 12.0, "bond_pct": 10.0},
    },
    "LARCH-2018-2": {
        "name": "Larchmere CLO 2018-2", "status": "AMORTIZING", "closed": "2018-09-20",
        "reinvest_end": "2022-10-20",
        "balances": {"AAA": 160e6, "AA": 55e6, "A": 30e6, "BBB": 30e6, "BB": 25e6, "EQ": 45e6},
        "oc": {"oc_ab": 1.340, "oc_c": 1.235, "oc_d": 1.145, "oc_e": 1.1225},
        "ic": {"ic_ab": 1.20, "ic_c": 1.15, "ic_d": 1.10, "ic_e": 1.05},
        "limits": {"ccc_pct": 7.5, "warf": 3400.0, "was": 3.25, "wal": 6.5, "diversity": 38.0,
                   "obligor_pct": 3.5, "industry_pct": 15.0, "second_lien_pct": 12.0, "bond_pct": 10.0},
    },
}  # fmt: skip
SPREAD_BPS = {"AAA": 145, "AA": 190, "A": 250, "BBB": 360, "BB": 650}
FEE_RATE = 0.0040  # senior fees, a year, on performing par
DEFAULT_PRICE_FLOOR = 38.0


def business_days(start: date, end: date) -> list[date]:
    """Return the weekdays from ``start`` to ``end`` inclusive."""
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    return [day for day in days if day.weekday() < 5]


def month_ends() -> list[date]:
    """Return the last weekday of each month from FIRST to LAST."""
    out = []
    year, month = FIRST.year, FIRST.month
    while (year, month) <= (LAST.year, LAST.month):
        nxt = date(year + month // 12, month % 12 + 1, 1)
        last = nxt - timedelta(1)
        while last.weekday() >= 5:
            last -= timedelta(1)
        out.append(last)
        year, month = nxt.year, nxt.month
    return out


def payment_date(year: int, month: int) -> date:
    """Return the 20th of the month, or the next business day (following convention)."""
    day = date(year, month, 20)
    while day.weekday() >= 5:
        day += timedelta(1)
    return day


def sofr_path(days: list[date], rng: random.Random) -> dict[date, float]:
    """Return fictional daily SOFR in percent: steps down from 5.33 with a little noise."""
    steps = [(date(2023, 1, 1), 5.33), (date(2024, 9, 19), 4.83), (date(2024, 11, 8), 4.58),
             (date(2024, 12, 19), 4.33), (date(2025, 9, 18), 4.08), (date(2025, 10, 30), 3.88),
             (date(2025, 12, 11), 3.65), (date(2026, 6, 18), 3.55)]  # fmt: skip
    out = {}
    for day in days:
        level = [rate for since, rate in steps if since <= day][-1]
        out[day] = round(level + rng.gauss(0, 0.015), 3)
    return out


class Universe:
    """The loans, obligors, ratings, and prices that both deals draw on."""

    def __init__(self, rng: random.Random) -> None:
        self.industries = [(f"LM{n + 1:02d}", name) for n, name in enumerate(INDUSTRIES)]
        weights = [9, 4, 3, 3, 8, 5, 3, 2, 3, 3, 4, 3, 4, 8, 6, 6, 3, 7, 2, 2, 2, 5, 5, 3, 3]
        names = [f"{a} {b}" for a in WORDS_A for b in WORDS_B]
        rng.shuffle(names)
        self.obligors: list[tuple[str, str, str, str]] = []
        for n in range(150):
            industry = rng.choices(self.industries, weights)[0][0]
            country = rng.choices(["US", "CA", "GB"], [90, 5, 5])[0]
            self.obligors.append((f"OB{n + 1:03d}", names[n], industry, country))
        picks = [rng.randrange(150) for _ in range(50)]
        order = [*range(150), *picks]
        types = ["FIRST_LIEN"] * 170 + ["SECOND_LIEN"] * 18 + ["BOND"] * 12
        rng.shuffle(types)
        initial = rng.choices(
            ["Ba2", "Ba3", "B1", "B2", "B3", "Caa1", "Caa2"], [5, 9, 26, 30, 21, 2, 0.01], k=150
        )
        self.initial_rating = {o[0]: r for o, r in zip(self.obligors, initial, strict=True)}
        self.loans: list[dict[str, Any]] = []
        for n, (o, kind) in enumerate(zip(order, types, strict=True)):
            obligor = self.obligors[o]
            rating = self.initial_rating[obligor[0]]
            base = {"Ba": 235, "B1": 285, "B2": 335, "B3": 385, "Ca": 500}[rating[:2]]
            spread = base + rng.choice([-25, 0, 25]) + (375 if kind == "SECOND_LIEN" else 0)
            year = rng.randint(2028, 2031) + (1 if kind == "SECOND_LIEN" else 0)
            self.loans.append({
                "loan_id": f"LN{n + 1:04d}", "obligor_id": obligor[0], "facility_type": kind,
                "spread_bps": 0 if kind == "BOND" else spread,
                "floor_bps": {"FIRST_LIEN": 50, "SECOND_LIEN": 75, "BOND": 0}[kind],
                "fixed_coupon_bps": rng.randint(725, 925) if kind == "BOND" else None,
                "maturity": date(year, rng.randint(1, 12), rng.randint(1, 28)),
            })  # fmt: skip
        self.by_id = {loan["loan_id"]: loan for loan in self.loans}
        self.industry_of = {o[0]: o[2] for o in self.obligors}
        self.rating_events: list[tuple[str, date, str]] = []

    def script_events(self, rng: random.Random, held_b: list[str], held_a: list[str]) -> None:
        """Add the rating changes: random drift, and the planted stress in the 2018-2 pool."""
        months = month_ends()
        scripted = set()
        own = defaultdict(list)
        for loan_id in held_b:
            own[self.by_id[loan_id]["obligor_id"]].append(loan_id)
        singles = [o for o, ls in own.items() if len(ls) == 1 and self.initial_rating[o] in ("B3", "B2")]
        rng.shuffle(singles)
        stress = singles[:14]
        for k, obligor in enumerate(stress):  # downgrades to Caa1 in Dec 2024-Feb 2025
            when = date(2024, 12, 9) + timedelta(days=5 * k)
            self.rating_events.append((obligor, when, "Caa1"))
            if k < 12:  # upgrades back by Jun 2025
                self.rating_events.append((obligor, date(2025, 6, 2) + timedelta(days=3 * k), "B3"))
        self.rating_events.append((stress[12], date(2025, 1, 21), "D"))  # one default, not cured
        self.rating_events = [e for e in self.rating_events if not (e[0] == stress[12] and e[2] == "B3")]
        scripted.update(stress)
        # 2021-1: three downgrades into CCC, one default late 2025 (a pool in good order).
        a_singles = [self.by_id[x]["obligor_id"] for x in held_a if self.by_id[x]["obligor_id"] not in scripted]
        rng.shuffle(a_singles)
        for k, obligor in enumerate(a_singles[:4]):
            self.rating_events.append((obligor, date(2025, 3, 12) + timedelta(days=7 * k), "Caa1"))
            scripted.add(obligor)
        self.rating_events.append((a_singles[4], date(2025, 11, 14), "D"))
        scripted.add(a_singles[4])
        for obligor, rating in self.initial_rating.items():
            if obligor in scripted:
                continue
            current = rating
            for month in months[1:]:
                roll = rng.random()
                idx = LADDER.index(current)
                if roll < 0.025 and idx < len(LADDER) - 1 and idx < 5:
                    current = LADDER[idx + 1]
                elif roll > 0.975 and idx > 0:
                    current = LADDER[idx - 1]
                else:
                    continue
                self.rating_events.append((obligor, month - timedelta(rng.randint(0, 20)), current))
        self.rating_events.sort(key=lambda e: (e[1], e[0]))

    def rating_on(self, obligor: str, day: date) -> str:
        """Return the rating in force on ``day``."""
        rating = self.initial_rating[obligor]
        for who, when, new in self.rating_events:
            if who == obligor and when <= day:
                rating = new
        return rating


def simulate_prices(uni: Universe, days: list[date], rng: random.Random) -> dict[date, dict[str, float]]:
    """Return daily loan prices: a mean-reverting walk, a market factor, and rating jumps."""
    target = {"Ba": 99.6, "B1": 99.2, "B2": 98.6, "B3": 97.2, "Ca": 82.0, "D": 44.0}
    vol = {"Ba": 0.07, "B1": 0.09, "B2": 0.12, "B3": 0.18, "Ca": 0.45, "D": 0.6}
    events = defaultdict(list)
    for who, when, new in uni.rating_events:
        events[when].append((who, new))
    rating = dict(uni.initial_rating)
    price = {}
    for loan in uni.loans:
        key = rating[loan["obligor_id"]][:2]
        price[loan["loan_id"]] = target[key] + rng.gauss(0, 0.5)
        if loan["facility_type"] == "BOND":
            price[loan["loan_id"]] -= 0.8
    out = {}
    for day in days:
        market = rng.gauss(0, 0.05) + (-0.12 if date(2025, 3, 24) <= day <= date(2025, 4, 14) else 0)
        market += 0.1 if date(2025, 4, 15) <= day <= date(2025, 5, 2) else 0
        for who, new in events.get(day, []):
            old = rating[who]
            rating[who] = new
            for loan in uni.loans:
                if loan["obligor_id"] == who:
                    drop = 0.0
                    if new == "D":
                        price[loan["loan_id"]] = rng.uniform(42, 52)
                    elif RATING_FACTOR[new] > RATING_FACTOR[old]:
                        drop = rng.uniform(4, 8) if new.startswith("Ca") else rng.uniform(0.5, 1.5)
                    else:
                        drop = -rng.uniform(1, 3)
                    price[loan["loan_id"]] -= drop
        for loan in uni.loans:
            key = rating[loan["obligor_id"]][:2]
            p = price[loan["loan_id"]]
            p += 0.03 * (target[key] - p) + rng.gauss(0, vol[key]) + market
            ceiling = 101.0 if key != "D" else 70.0
            price[loan["loan_id"]] = min(max(p, DEFAULT_PRICE_FLOOR), ceiling)
        out[day] = {k: round(v, 3) for k, v in price.items()}
    return out


# ---- the independent reference calculation, from plain rows ------------------------------------


def years_between(start: date, end: date) -> float:
    """Return actual days over 365.25."""
    return (end - start).days / 365.25


def reference_tests(
    day: date, rows: list[dict[str, Any]], balances: dict[str, float], deal: dict[str, Any],
    sofr: float, cash: float,
) -> dict[str, float]:  # fmt: skip
    """Return every test's value for one snapshot, written plainly, as the conventions say.

    ``rows``: loan_id, par, price, rf, spread_bps, floor_bps, fixed_bps, maturity, industry,
    obligor, facility_type. ``sofr`` is a fraction. See `known_answers.json` for the conventions.
    """
    perf = [r for r in rows if r["rf"] < DEFAULT_RF and r["par"] > 0]
    dflt = [r for r in rows if r["rf"] >= DEFAULT_RF and r["par"] > 0]
    perf_par = math.fsum(r["par"] for r in perf)
    ccc = sorted((r for r in perf if r["rf"] >= CCC_RF), key=lambda r: r["price"])
    ccc_par = math.fsum(r["par"] for r in ccc)
    excess = max(ccc_par - 0.075 * perf_par, 0.0)
    haircut = 0.0
    for r in ccc:
        take = min(r["par"], excess)
        haircut += take * (1 - r["price"] / 100)
        excess -= take
    adjusted = perf_par - haircut + math.fsum(r["par"] * r["price"] / 100 for r in dflt) + cash
    out: dict[str, float] = {"adjusted_par": adjusted, "ccc_haircut": haircut}
    for name, classes in OC_CLASSES.items():
        out[name] = adjusted / math.fsum(balances[t] for t in classes)
    income = math.fsum(
        r["par"] * (r["fixed_bps"] / 1e4 if r["fixed_bps"] is not None
                    else max(sofr, r["floor_bps"] / 1e4) + r["spread_bps"] / 1e4)
        for r in perf
    )  # fmt: skip
    for name, classes in IC_CLASSES.items():
        out[name] = income / math.fsum(balances[t] * (sofr + SPREAD_BPS[t] / 1e4) for t in classes)
    out["ccc_pct"] = 100 * ccc_par / perf_par
    out["warf"] = math.fsum(r["par"] * r["rf"] for r in perf) / perf_par
    floating = [r for r in perf if r["fixed_bps"] is None]
    out["was"] = math.fsum(
        r["par"] * (r["spread_bps"] + max(r["floor_bps"] - sofr * 1e4, 0)) for r in floating
    ) / math.fsum(r["par"] for r in floating) / 100  # fmt: skip
    out["wal"] = math.fsum(r["par"] * years_between(day, r["maturity"]) for r in perf) / perf_par
    by_obligor: dict[str, float] = defaultdict(float)
    obligor_industry = {}
    for r in perf:
        by_obligor[r["obligor"]] += r["par"]
        obligor_industry[r["obligor"]] = r["industry"]
    average = perf_par / len(by_obligor)
    units: dict[str, float] = defaultdict(float)
    for obligor, amount in by_obligor.items():
        units[obligor_industry[obligor]] += min(1.0, amount / average)
    score = 0.0
    for u in units.values():
        u = min(u, 10.0)
        lo = int(u)
        score += DS_TABLE[lo] + (DS_TABLE[min(lo + 1, 10)] - DS_TABLE[lo]) * (u - lo)
    out["diversity"] = score
    out["obligor_pct"] = 100 * max(by_obligor.values()) / perf_par
    by_industry: dict[str, float] = defaultdict(float)
    for obligor, amount in by_obligor.items():
        by_industry[obligor_industry[obligor]] += amount
    out["industry_pct"] = 100 * max(by_industry.values()) / perf_par
    for name, kind in (("second_lien_pct", "SECOND_LIEN"), ("bond_pct", "BOND")):
        out[name] = 100 * math.fsum(r["par"] for r in perf if r["facility_type"] == kind) / perf_par
    return out


def outcome(deal: dict[str, Any], values: dict[str, float]) -> dict[str, tuple[float, str]]:
    """Return each test's (trigger, PASS or FAIL)."""
    out = {}
    for name in (*MIN_TESTS, *MAX_TESTS):
        level = deal["oc"].get(name) or deal["ic"].get(name) or deal["limits"][name]
        passed = values[name] >= level if name in MIN_TESTS else values[name] <= level
        out[name] = (level, "PASS" if passed else "FAIL")
    return out


# ---- the deals ---------------------------------------------------------------------------------


class DealSim:
    """One deal's month-by-month pool, tranches, cash, and waterfall."""

    def __init__(self, deal_id: str, uni: Universe, held: list[str], rng: random.Random) -> None:
        self.id, self.deal, self.uni, self.rng = deal_id, DEALS[deal_id], uni, rng
        target = 498e6 if deal_id.endswith("2021-1") else 338e6
        sizes = [min(max(rng.lognormvariate(1.2, 0.45), 1.5), 5.0) for _ in held]
        scale = target / (sum(sizes) * 1e6)
        self.par = {h: round(s * 1e6 * scale / 1000) * 1000.0 for h, s in zip(held, sizes, strict=True)}
        self.balance = dict(self.deal["balances"])
        self.principal_cash = 2.0e6 if deal_id.endswith("2021-1") else 1.0e6
        self.interest_cash = 0.0

    def rows(self, day: date, prices: dict[str, float], extra: bool = False) -> list[dict[str, Any]]:
        out = []
        for loan_id in (self.uni.by_id if extra else self.par):
            loan = self.uni.by_id[loan_id]
            rating = self.uni.rating_on(loan["obligor_id"], day)
            out.append({
                "loan_id": loan_id, "par": self.par.get(loan_id, 0.0), "price": prices[loan_id],
                "rf": RATING_FACTOR[rating], "spread_bps": loan["spread_bps"],
                "floor_bps": loan["floor_bps"], "fixed_bps": loan["fixed_coupon_bps"],
                "maturity": loan["maturity"], "industry": self.uni.industry_of[loan["obligor_id"]],
                "obligor": loan["obligor_id"], "facility_type": loan["facility_type"],
            })  # fmt: skip
        return out


def interest_rate(row: dict[str, Any], sofr: float) -> float:
    """Return a loan's annual coupon as a fraction."""
    if row["fixed_bps"] is not None:
        return float(row["fixed_bps"]) / 1e4
    return max(sofr, row["floor_bps"] / 1e4) + row["spread_bps"] / 1e4


def run(seed: int) -> dict[str, Any]:
    """Simulate everything and return the tables as plain Python rows."""
    rng = random.Random(seed)
    uni = Universe(rng)
    ids = [loan["loan_id"] for loan in uni.loans]
    shuffled = ids[:]
    rng.shuffle(shuffled)
    held = {"LARCH-2021-1": shuffled[:125], "LARCH-2018-2": shuffled[75:180]}
    uni.script_events(rng, held["LARCH-2018-2"], held["LARCH-2021-1"])
    days = business_days(FIRST - timedelta(31), LAST)
    sofr = sofr_path(business_days(date(2023, 9, 1), LAST), rng)
    prices = simulate_prices(uni, [d for d in days if d >= FIRST - timedelta(31)], rng)
    reports = month_ends()
    data: dict[str, Any] = {
        "uni": uni, "sofr": sofr, "prices": prices, "reports": reports, "held": held,
        "holdings": [], "balances": [], "cash": [], "tests": [], "cashflows": [], "nav": [],
        "values": {},
    }  # fmt: skip
    for deal_id in DEALS:
        sim = DealSim(deal_id, uni, held[deal_id], random.Random(seed + len(deal_id) + hash_str(deal_id)))
        simulate_deal(sim, data, reports, prices, sofr)
    return data


def hash_str(text: str) -> int:
    """Return a small stable integer for ``text`` (Python's hash() is salted)."""
    return sum(ord(c) * (n + 1) for n, c in enumerate(text))


def simulate_deal(sim: DealSim, data: dict[str, Any], reports: list[date], prices: Any, sofr: Any) -> None:
    """Run one deal over all report dates."""
    rng, uni, deal = sim.rng, sim.uni, sim.deal
    reinvest = deal["status"] == "REINVESTING"
    last_pay = date(2023, 10, 20)
    prev_tests: dict[str, tuple[float, str]] = {}
    prev_nav: float | None = None
    for index, day in enumerate(reports):
        px = prices[day]
        sofr_now = sofr[day] / 100
        pay_here = day.month in (1, 4, 7, 10) and index > 0
        dist_month = 0.0
        if index > 0:
            before = reports[index - 1]
            rows = sim.rows(before, prices[before])
            accrual = math.fsum(r["par"] * interest_rate(r, sofr[before] / 100) / 12
                                for r in rows if r["rf"] < DEFAULT_RF)  # fmt: skip
            # month events: repayments, sales of defaulted loans, purchases
            for loan_id in list(sim.par):
                loan = uni.by_id[loan_id]
                rating = uni.rating_on(loan["obligor_id"], day)
                if RATING_FACTOR[rating] >= DEFAULT_RF and reinvest and rng.random() < 0.6:
                    sim.principal_cash += sim.par.pop(loan_id) * px[loan_id] / 100 * 1.0
                elif RATING_FACTOR[rating] < DEFAULT_RF and rng.random() < (0.012 if reinvest else 0.008):
                    sim.principal_cash += sim.par.pop(loan_id)
            if reinvest:
                pool = [x for x in uni.by_id if x not in sim.par and uni.rating_on(uni.by_id[x]["obligor_id"], day) in LADDER[:6]]
                obligors_held = {uni.by_id[x]["obligor_id"] for x in sim.par}
                pool = [x for x in pool if uni.by_id[x]["obligor_id"] not in obligors_held]
                while sim.principal_cash > 1.5e6 and pool:
                    pick = pool.pop(rng.randrange(len(pool)))
                    amount = min(round(rng.uniform(2e6, 5e6) / 1000) * 1000.0, sim.principal_cash / (px[pick] / 100))
                    amount = math.floor(amount / 1000) * 1000.0
                    sim.par[pick] = amount
                    sim.principal_cash -= amount * px[pick] / 100
            if pay_here:
                dist_month = pay(sim, data, day, before, prev_tests, accrual, last_pay, sofr)
                last_pay = payment_date(day.year, day.month)
                sim.interest_cash = accrual * 10 / 30
            else:
                sim.interest_cash += accrual
        rows = sim.rows(day, px)
        values = reference_tests(day, rows, sim.balance, deal, sofr_now, sim.principal_cash)
        prev_tests = outcome(deal, values)
        data["values"][sim.id, day] = (values, prev_tests)
        for r in rows:
            data["holdings"].append((day, sim.id, r["loan_id"], r["par"], px[r["loan_id"]]))
        for t in TRANCHES:
            data["balances"].append((sim.id, day, t, sim.balance[t]))
        data["cash"].append((sim.id, day, sim.principal_cash, sim.interest_cash))
        days_acc = (day - last_pay).days
        perf_par = math.fsum(r["par"] for r in rows if r["rf"] < DEFAULT_RF)
        accrued = math.fsum(sim.balance[t] * (sofr[last_pay] / 100 + SPREAD_BPS[t] / 1e4) * days_acc / 360 for t in DEBT)
        accrued += perf_par * FEE_RATE * days_acc / 360
        assets = math.fsum(r["par"] * r["price"] / 100 for r in rows)
        nav = assets + sim.principal_cash + sim.interest_cash - math.fsum(sim.balance[t] for t in DEBT) - accrued
        ret = None if prev_nav is None else (nav - prev_nav + dist_month) / prev_nav
        data["nav"].append((sim.id, day, nav, ret, dist_month))
        prev_nav = nav
        for name, (level, result) in prev_tests.items():
            data["tests"].append((sim.id, day, name, values[name], level, result))


def pay(sim: DealSim, data: dict[str, Any], day: date, before: date, tests: dict[str, tuple[float, str]],
        accrual: float, last_pay: date, sofr: Any) -> float:  # fmt: skip
    """Run the interest and principal waterfall on this month's payment date; return the equity share."""
    when = payment_date(day.year, day.month)
    days = (when - last_pay).days
    reset = sofr[last_pay] / 100
    rows = sim.rows(before, data["prices"][before])
    perf_par = math.fsum(r["par"] for r in rows if r["rf"] < DEFAULT_RF)
    available = sim.interest_cash + accrual * 20 / 30 - perf_par * FEE_RATE * days / 360
    diverted = any(tests[t][1] == "FAIL" for t in OC_CLASSES)
    paid_interest = {}
    for t in DEBT:
        due = sim.balance[t] * (reset + SPREAD_BPS[t] / 1e4) * days / 360
        paid_interest[t] = min(due, max(available, 0.0))
        available -= paid_interest[t]
    principal = {t: 0.0 for t in TRANCHES}
    principal_pool = 0.0 if sim.deal["status"] == "REINVESTING" else sim.principal_cash
    sim.principal_cash -= principal_pool
    equity = 0.0
    if diverted:
        principal_pool += max(available, 0.0)
    else:
        equity = max(available, 0.0)
    for t in DEBT:
        take = min(principal_pool, sim.balance[t])
        principal[t], principal_pool = take, principal_pool - take
        sim.balance[t] -= take
    sim.principal_cash += principal_pool
    for t in DEBT:
        data["cashflows"].append((sim.id, when, t, paid_interest[t], principal[t]))
    data["cashflows"].append((sim.id, when, "EQ", equity, 0.0))
    return equity


# ---- writing the estate ------------------------------------------------------------------------

SQLITE_DDL = """
CREATE TABLE deal (deal_id TEXT PRIMARY KEY, name TEXT, status TEXT, closing_date TEXT, reinvestment_end TEXT);
CREATE TABLE tranche (deal_id TEXT, tranche TEXT, seniority INTEGER, original_par REAL, spread_bps INTEGER,
  oc_trigger REAL, ic_trigger REAL, PRIMARY KEY (deal_id, tranche));
CREATE TABLE test_limit (deal_id TEXT, test_name TEXT, level REAL, PRIMARY KEY (deal_id, test_name));
CREATE TABLE industry (industry_code TEXT PRIMARY KEY, name TEXT);
CREATE TABLE obligor (obligor_id TEXT PRIMARY KEY, name TEXT, industry_code TEXT, country TEXT);
CREATE TABLE loan (loan_id TEXT PRIMARY KEY, obligor_id TEXT, facility_type TEXT, spread_bps INTEGER,
  floor_bps INTEGER, fixed_coupon_bps INTEGER, maturity_date TEXT);
CREATE TABLE rating_history (obligor_id TEXT, effective_date TEXT, rating TEXT, PRIMARY KEY (obligor_id, effective_date));
CREATE TABLE holdings (report_date TEXT, deal_id TEXT, loan_id TEXT, par REAL, market_price REAL,
  PRIMARY KEY (report_date, deal_id, loan_id));
CREATE TABLE tranche_balance (deal_id TEXT, report_date TEXT, tranche TEXT, balance REAL,
  PRIMARY KEY (deal_id, report_date, tranche));
CREATE TABLE cash_balance (deal_id TEXT, report_date TEXT, principal_cash REAL, interest_cash REAL,
  PRIMARY KEY (deal_id, report_date));
CREATE TABLE equity_nav (deal_id TEXT, report_date TEXT, nav REAL, monthly_return REAL, distribution REAL,
  PRIMARY KEY (deal_id, report_date));
CREATE TABLE rating_scale (rating TEXT PRIMARY KEY, rating_factor INTEGER);
"""
# The views the store reads: the same names as views.sql (schema `clo`, here an attached file).
SQLITE_VIEWS = """
CREATE VIEW v_loan_static AS SELECT l.loan_id, l.obligor_id, o.industry_code, l.facility_type, l.spread_bps,
  l.floor_bps, COALESCE(l.fixed_coupon_bps, 0) AS fixed_coupon_bps,
  CASE WHEN l.fixed_coupon_bps IS NULL THEN 0 ELSE 1 END AS is_fixed,
  CAST(julianday(l.maturity_date) - 2440587.5 AS INTEGER) AS maturity_epoch_day
  FROM loan l JOIN obligor o ON o.obligor_id = l.obligor_id;
CREATE VIEW v_holdings AS SELECT report_date, deal_id, loan_id, par, market_price FROM holdings;
CREATE VIEW v_loan_rating_factor AS SELECT h.report_date, h.loan_id, s.rating_factor
  FROM (SELECT DISTINCT report_date, loan_id FROM holdings) h
  JOIN loan l ON l.loan_id = h.loan_id
  JOIN rating_history r ON r.obligor_id = l.obligor_id
   AND r.effective_date = (SELECT MAX(effective_date) FROM rating_history x
        WHERE x.obligor_id = l.obligor_id AND x.effective_date <= h.report_date)
  JOIN rating_scale s ON s.rating = r.rating;
CREATE VIEW v_structure AS SELECT t.deal_id, b.report_date, t.tranche, b.balance, t.spread_bps,
  t.oc_trigger, t.ic_trigger FROM tranche t JOIN tranche_balance b ON b.deal_id = t.deal_id AND b.tranche = t.tranche;
CREATE VIEW v_cash AS SELECT deal_id, report_date, principal_cash, interest_cash FROM cash_balance;
CREATE VIEW v_test_limit AS SELECT deal_id, test_name, level FROM test_limit;
CREATE VIEW v_equity_nav AS SELECT deal_id, report_date, nav, monthly_return, distribution FROM equity_nav;
CREATE VIEW v_rating_history AS SELECT obligor_id, effective_date, rating FROM rating_history;
"""


def iso(day: date) -> str:
    """Return ``day`` as YYYY-MM-DD."""
    return day.isoformat()


def write_sql(path: Path, data: dict[str, Any]) -> None:
    """Write the SQLite stand-in for the Azure SQL database."""
    uni: Universe = data["uni"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SQLITE_DDL)
        for deal_id, d in DEALS.items():
            db.execute("INSERT INTO deal VALUES (?,?,?,?,?)", (deal_id, d["name"], d["status"], d["closed"], d["reinvest_end"]))
            for n, t in enumerate(TRANCHES):
                oc = {"AAA": d["oc"]["oc_ab"], "AA": d["oc"]["oc_ab"], "A": d["oc"]["oc_c"],
                      "BBB": d["oc"]["oc_d"], "BB": d["oc"]["oc_e"], "EQ": None}[t]  # fmt: skip
                ic = {"AAA": d["ic"]["ic_ab"], "AA": d["ic"]["ic_ab"], "A": d["ic"]["ic_c"],
                      "BBB": d["ic"]["ic_d"], "BB": d["ic"]["ic_e"], "EQ": None}[t]  # fmt: skip
                db.execute("INSERT INTO tranche VALUES (?,?,?,?,?,?,?)",
                           (deal_id, t, n + 1, d["balances"][t], SPREAD_BPS.get(t), oc, ic))  # fmt: skip
            for name, level in d["limits"].items():
                db.execute("INSERT INTO test_limit VALUES (?,?,?)", (deal_id, name, level))
        db.executemany("INSERT INTO industry VALUES (?,?)", uni.industries)
        db.executemany("INSERT INTO obligor VALUES (?,?,?,?)", [(o[0], o[1], o[2], o[3]) for o in uni.obligors])
        db.executemany("INSERT INTO loan VALUES (?,?,?,?,?,?,?)", [
            (x["loan_id"], x["obligor_id"], x["facility_type"], x["spread_bps"], x["floor_bps"],
             x["fixed_coupon_bps"], iso(x["maturity"])) for x in uni.loans])  # fmt: skip
        history = [(o, iso(date(2023, 10, 1)), r) for o, r in uni.initial_rating.items()]
        history += [(o, iso(w), r) for o, w, r in uni.rating_events]
        db.executemany("INSERT OR REPLACE INTO rating_history VALUES (?,?,?)", history)
        db.executemany("INSERT INTO rating_scale VALUES (?,?)", RATING_FACTOR.items())
        db.executemany("INSERT INTO holdings VALUES (?,?,?,?,?)", [(iso(a), b, c, d, e) for a, b, c, d, e in data["holdings"]])
        db.executemany("INSERT INTO tranche_balance VALUES (?,?,?,?)", [(a, iso(b), c, d) for a, b, c, d in data["balances"]])
        db.executemany("INSERT INTO cash_balance VALUES (?,?,?,?)", [(a, iso(b), c, d) for a, b, c, d in data["cash"]])
        db.executemany("INSERT INTO equity_nav VALUES (?,?,?,?,?)", [(a, iso(b), c, d, e) for a, b, c, d, e in data["nav"]])
        db.executescript(SQLITE_VIEWS)


def write_table(root: Path, table: str, partition: str, groups: dict[str, list[dict[str, Any]]]) -> str:
    """Write one curated table, one folder per partition value; return the file format used."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        pa = None
    for value, rows in groups.items():
        folder = root / table / f"{partition}={value}"
        folder.mkdir(parents=True, exist_ok=True)
        if pa is None:
            with (folder / "part-0000.csv").open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            columns = {k: [r[k] for r in rows] for k in rows[0]}
            pq.write_table(pa.table(columns), folder / "part-0000.parquet", compression="zstd")
    return "csv" if pa is None else "parquet"


def write_lake(out: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Write the raw and curated containers, and return the catalog."""
    uni: Universe = data["uni"]
    raw, curated = out / "lake" / "raw", out / "lake" / "curated"
    by_day: dict[tuple[str, date], list[tuple[Any, ...]]] = defaultdict(list)
    for deal_id, day, name, value, level, result in data["tests"]:
        by_day[deal_id, day].append((name, value, level, result))
    tests_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (deal_id, day), items in sorted(by_day.items()):
        folder = raw / "trustee" / f"{day:%Y-%m}"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"larchmere_{deal_id.lower()}_{day:%Y%m%d}_tests.csv").open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["deal_id", "report_date", "test_name", "reported_value", "trigger_level", "result"])
            for name, value, level, result in items:
                reported = reported_value(deal_id, name, value)
                writer.writerow([deal_id, iso(day), name, f"{reported:.6f}", f"{level:.6f}", result])
                tests_groups[iso(day)].append(
                    {"deal_id": deal_id, "test_name": name, "reported_value": round(reported, 6),
                     "trigger_level": level, "result": result}
                )  # fmt: skip
    fmt = write_table(curated, "trustee_tests", "dt", tests_groups)
    daily = [d for d in sorted(data["prices"]) if d >= DAILY_FROM]
    price_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day in daily:
        for loan_id, price in data["prices"][day].items():
            price_groups[f"{day:%Y-%m}"].append({"price_date": iso(day), "loan_id": loan_id, "price": price})
    write_table(curated, "loan_prices", "month", price_groups)
    cf_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for deal_id, when, tranche, interest, principal in data["cashflows"]:
        cf_groups[iso(when)].append({"deal_id": deal_id, "tranche": tranche,
                                     "interest_paid": round(interest, 2), "principal_paid": round(principal, 2)})  # fmt: skip
    write_table(curated, "tranche_cashflows", "dt", cf_groups)
    sofr_groups = {f"{d:%Y-%m}": [] for d in data["sofr"]}
    for day, rate in data["sofr"].items():
        sofr_groups[f"{day:%Y-%m}"].append({"rate_date": iso(day), "sofr_pct": rate})
    write_table(curated, "sofr", "month", sofr_groups)
    host = f"{ACCOUNT}.dfs.core.windows.net"
    catalog = {
        "account": ACCOUNT,
        "uris": {
            f"abfss://raw@{host}": "lake/raw",
            f"abfss://curated@{host}": "lake/curated",
            f"https://{ACCOUNT}.blob.core.windows.net/research": "lake/docs",
        },
        "format": fmt,
        "tables": {
            "trustee_tests": {"location": f"abfss://curated@{host}/trustee_tests/", "partition": "dt",
                              "columns": ["deal_id", "test_name", "reported_value", "trigger_level", "result"]},
            "loan_prices": {"location": f"abfss://curated@{host}/loan_prices/", "partition": "month",
                            "columns": ["price_date", "loan_id", "price"]},
            "tranche_cashflows": {"location": f"abfss://curated@{host}/tranche_cashflows/", "partition": "dt",
                                  "columns": ["deal_id", "tranche", "interest_paid", "principal_paid"]},
            "sofr": {"location": f"abfss://curated@{host}/sofr/", "partition": "month",
                     "columns": ["rate_date", "sofr_pct"]},
        },
        "note": "Production would be Delta tables (a _delta_log folder); these are plain Parquet.",
    }  # fmt: skip
    (out / "lake").mkdir(parents=True, exist_ok=True)
    (out / "lake" / "catalog.json").write_text(json.dumps(catalog, indent=2))
    return catalog


def reported_value(deal_id: str, name: str, value: float) -> float:
    """Return what the trustee reports: the same, but a diversity score by Moody's own table."""
    return value + 1.8 + (hash_str(deal_id + name) % 7) / 10 if name == "diversity" else value


def write_docs(out: Path, source: Path) -> None:
    """Copy the research documents to the SharePoint export, adding one .docx as SharePoint holds."""
    docs = out / "lake" / "docs"
    if source.is_dir():
        for path in sorted(source.glob("*.md")):
            target = docs / "CLO Research" / "Shared Documents" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text())
    word = docs / "CLO Research" / "Shared Documents" / "IC-pack-2026-09.docx"
    word.parent.mkdir(parents=True, exist_ok=True)
    body = ("<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>"
            "<w:p><w:r><w:t>Investment committee pack, September 2026 (fictional).</w:t></w:r></w:p>"
            "</w:body></w:document>")  # fmt: skip
    with zipfile.ZipFile(word, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", body)


def planted(data: dict[str, Any]) -> dict[str, Any]:
    """Return the planted truths with their conventions."""
    values = data["values"]
    result: dict[str, Any] = {
        "conventions": {
            "units": "par and balances in USD; prices in points of par; rates as fractions; SOFR in percent in the lake",
            "oc": "adjusted collateral par / balance of the class and everything senior; adjusted par = performing par "
                  "- CCC excess haircut + defaulted par at market price + principal cash",
            "ccc": "performing loans rated Caa1 or lower (rating factor 4770 to 9999); excess over 7.5% of performing par is carried at "
                   "market price, lowest priced first",
            "performing": "rating factor below 10000; Ca, C and D (factor 10000) are defaulted",
            "ic": "annual interest on performing par (floors applied; bonds at their fixed coupon) / annual interest on the class and "
                  "everything senior at SOFR + spread; fees excluded",
            "warf": "par-weighted rating factor of performing loans, Moody's published factors",
            "was": "bps, par-weighted over performing floating loans, spread plus max(floor - SOFR, 0)",
            "wal": "years, par-weighted over performing loans, actual days / 365.25 to maturity",
            "diversity": "simplified: obligor units min(1, par / average obligor par); industry score by linear interpolation of "
                         "1:1.0 2:1.5 3:2.0 4:2.33 5:2.67 6:3.0 7:3.25 8:3.5 9:3.75 10+:4.0; trustee reports Moody's own, "
                         "about 2 to 2.5 points higher",
            "equity_return": "(NAV change + distributions in the month) / prior NAV; NAV = loans at market + cash - senior "
                             "tranche par - accrued tranche interest and fees",
        },
        "tests": {},
        "failures": {},
    }
    for (deal_id, day), (vals, outcomes) in sorted(values.items()):
        failed = [name for name, (_, verdict) in outcomes.items() if verdict == "FAIL"]
        if failed:
            result["failures"].setdefault(deal_id, {})[iso(day)] = failed
    for deal_id in DEALS:
        last = data["reports"][-1]
        result["tests"][deal_id] = {iso(last): values[deal_id, last][0]}
        mid = date(2025, 2, 28)
        mid_day = next(d for d in data["reports"] if d >= mid)
        result["tests"][deal_id][iso(mid_day)] = values[deal_id, mid_day][0]
    zero = [(deal, iso(when)) for deal, when, t, i, p in data["cashflows"] if t == "EQ" and i == 0.0]
    result["zero_equity_distributions"] = zero
    result["distributions"] = {
        deal: {iso(when): round(i, 2) for d, when, t, i, p in data["cashflows"] if d == deal and t == "EQ"}
        for deal in DEALS
    }  # fmt: skip
    result["final_balances"] = {
        deal: {t: b for d, day, t, b in data["balances"] if d == deal and day == data["reports"][-1]} for deal in DEALS
    }  # fmt: skip
    result["equity_returns"] = {
        deal: {iso(day): ret for d, day, nav, ret, dist in data["nav"] if d == deal} for deal in DEALS
    }  # fmt: skip
    return result


def build(out: Path, seed: int = DEFAULT_SEED, research: Path | None = None) -> dict[str, Any]:
    """Build the estate under ``out`` and return the planted truths."""
    data = run(seed)
    out.mkdir(parents=True, exist_ok=True)
    write_sql(out / "sql" / "clo.db", data)
    catalog = write_lake(out, data)
    write_docs(out, research or Path(__file__).resolve().parents[1] / "research")
    answers = planted(data)
    answers["seed"] = seed
    answers["format"] = catalog["format"]
    (out / KNOWN_ANSWERS).write_text(json.dumps(answers, indent=1, sort_keys=True, default=str))
    return answers


def main() -> None:
    """Command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", type=Path, default=Path("larchmere-estate"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    arguments = parser.parse_args()
    build(arguments.out, arguments.seed)
    print(f"Built {arguments.out}")


if __name__ == "__main__":
    main()
