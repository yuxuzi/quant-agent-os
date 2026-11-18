"""Build Corvane Equity Partners' data estate: a local stand-in for its AWS and Snowflake setup.

    python build_corvane.py [OUT_DIR] [--seed N]

Everything is fictional and deterministic for a seed. The output directory holds:

- ``s3/corvane-raw/raw/...``: vendor files as they land (benchmark constituents with weights in
  percent, wide factor-score files).
- ``s3/corvane-curated/curated/<table>/dt=YYYY-MM-DD/part-0000.parquet``: the Glue job's output,
  Hive-partitioned by date (``prices_daily``, ``fx_daily``, ``factor_scores``), as Athena or a
  Snowflake external table reads it.
- ``s3/corvane-research/research/...``: the research documents (an S3 prefix).
- ``snowflake/CORVANE_CURATED.db`` and ``CORVANE_REF.db``: SQLite files that stand in for the
  Snowflake schemas ``CORVANE.CURATED`` and ``CORVANE.REF`` (holdings, trades, benchmark tables,
  accounts). SQLite is not Snowflake: identifiers are upper case as in Snowflake, dates are ISO text.
- ``catalog/glue_catalog.json``: the Glue Data Catalog's table definitions for the curated tables.
- ``s3_map.json``: which ``s3://`` bucket is which directory here.
- ``known_answers.json``: the truths planted in the data, each computed by code here that shares
  nothing with the platform or the fund's tools, with its convention.

Prices are price returns (no dividends are modelled), so a holding's value moves exactly with its
total return. Holdings are the books' end-of-day snapshots, after that day's trades.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shutil
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_SEED = 20261118
START = date(2026, 1, 2)
N_DAYS = 120
KNOWN_DATE = "2026-05-15"
CASH = "CASH_USD"
DAYS_PER_YEAR = 252
RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
BUCKETS = {
    "s3://corvane-raw": "s3/corvane-raw",
    "s3://corvane-curated": "s3/corvane-curated",
    "s3://corvane-research": "s3/corvane-research",
}
SECTORS: dict[str, tuple[str, str, str]] = {
    "Energy": ("Oil and Gas Producers", "Energy Services", "Refining"),
    "Materials": ("Chemicals", "Metals and Mining", "Packaging"),
    "Industrials": ("Machinery", "Aerospace", "Transport"),
    "Consumer Discretionary": ("Autos", "Retail", "Leisure"),
    "Consumer Staples": ("Food Products", "Beverages", "Tobacco"),
    "Health Care": ("Pharmaceuticals", "Devices", "Services"),
    "Financials": ("Banks", "Insurance", "Asset Managers"),
    "Information Technology": ("Software", "Hardware", "Semiconductors"),
    "Communication Services": ("Media", "Telecom", "Interactive"),
    "Utilities": ("Electric", "Gas", "Water"),
    "Real Estate": ("Office", "Residential", "Industrial REITs"),
}
FACTORS = ("quality", "value", "momentum", "lowvol")
ACCOUNTS = (
    # id, name, strategy, benchmark, client, aum, model
    ("LCC", "US Large Cap Core composite", "us_large_cap_core", "BM_US300", "(composite)", 1.5e9),
    ("GQ", "Global Quality composite", "global_quality", "BM_GLOBAL200", "(composite)", 0.9e9),
    ("SMA_PEN_A", "Aldergate Pension Plan", "us_large_cap_core", "BM_US300", "Aldergate", 4.0e8),
    ("SMA_FND_B", "Brightwater Foundation", "us_large_cap_core", "BM_US300", "Brightwater", 1.5e8),
    ("SMA_SWF_C", "Cantor Sovereign Fund", "global_quality", "BM_GLOBAL200", "Cantor", 3.0e8),
)
MODEL_OF = {"LCC": "LCC", "SMA_PEN_A": "LCC", "SMA_FND_B": "LCC", "GQ": "GQ", "SMA_SWF_C": "GQ"}
RESTRICTIONS = (
    ("SMA_FND_B", "exclude_industry", "Tobacco"),
    ("SMA_SWF_C", "exclude_industry", "Tobacco"),
    ("SMA_SWF_C", "max_position_weight", "0.05"),
    ("SMA_PEN_A", "max_position_weight", "0.07"),
    ("LCC", "max_position_weight", "0.07"),
)
MANAGEMENT_FEE = 0.004  # a year, accrued daily in the net return
CASH_TARGET = 0.015
HOLDINGS = {"LCC": 70, "GQ": 45}
CAP = {"LCC": 0.06, "GQ": 0.05}
SPLIT_DATE, SPIN_DATE, SPIN_SALE_DATE = "2026-02-18", "2026-03-12", "2026-03-18"
DELIST_ANNOUNCE, DELIST_DATE = "2026-04-06", "2026-04-22"
JP_HOLIDAYS = ("2026-02-11", "2026-03-20", "2026-04-29", "2026-05-04", "2026-05-05", "2026-05-06")
SPIN_RATIO = 4  # one spin-off share for four parent shares


@dataclass
class Security:
    """One fictional security."""

    sid: str
    name: str
    sector: str
    industry: str
    country: str
    currency: str
    mcap: float
    beta: float
    sigma: float
    price0: float
    alpha: float = 0.0
    first: int = 0  # first day index with a row
    last: int | None = None  # last day index with a row; None when it lasts
    rets: list[float | None] = field(default_factory=list)  # vendor return, by day
    close: list[float | None] = field(default_factory=list)  # raw close, None with no row
    volume: list[int | None] = field(default_factory=list)


def business_days(count: int) -> list[date]:
    """Return ``count`` weekdays from `START`."""
    days: list[date] = []
    day = START
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _syllable_name(rng: random.Random, used: set[str]) -> str:
    first = ("Al", "Bren", "Cor", "Dal", "El", "Fen", "Gar", "Hal", "Ir", "Jor", "Kel", "Lor", "Mar")
    mid = ("a", "e", "o", "in", "ar", "en", "ur", "av")
    last = ("vane", "mont", "ford", "ston", "wick", "dale", "ridge", "ton", "mere", "gate", "haven")
    kind = ("Holdings", "Group", "Industries", "Systems", "Partners", "Corp", "Labs", "Works")
    while True:
        name = f"{rng.choice(first)}{rng.choice(mid)}{rng.choice(last)} {rng.choice(kind)}"
        if name not in used:
            used.add(name)
            return name


def _make_universe(rng: random.Random) -> list[Security]:
    used: set[str] = set()
    out: list[Security] = []
    plan = [("US", "USD", 300), ("DE", "EUR", 20), ("FR", "EUR", 15), ("GB", "GBP", 25)]
    plan += [("JP", "JPY", 40)]
    sectors = list(SECTORS)
    for country, currency, count in plan:
        caps = sorted((math.exp(rng.gauss(23.4, 0.9)) for _ in range(count)), reverse=True)
        for number, mcap in enumerate(caps, start=1):
            sector = rng.choice(sectors)
            out.append(
                Security(
                    sid=f"{country}{number:04d}",
                    name=_syllable_name(rng, used),
                    sector=sector,
                    industry=rng.choice(SECTORS[sector][:2]),  # Tobacco is assigned on purpose
                    country=country,
                    currency=currency,
                    mcap=mcap,
                    beta=rng.uniform(0.7, 1.3),
                    sigma=rng.uniform(0.010, 0.018),
                    price0=math.exp(rng.gauss(4.0, 0.6)),
                )
            )
    return out


@dataclass
class Plan:
    """The planted securities and events."""

    parent: str
    split: str
    delist: str
    winners: list[str]
    tobacco: list[str]
    spinco: str


def _assign_roles(universe: list[Security]) -> Plan:
    us = [s for s in universe if s.country == "US"]  # largest first
    def force(security: Security, sector: str, industry: str) -> None:
        security.sector, security.industry = sector, industry

    for index in (0, 1, 2, 3, 4, 5):  # the largest names are never tobacco by accident
        if us[index].industry == "Tobacco":
            us[index].industry = SECTORS[us[index].sector][0]
    parent = us[12]
    force(parent, "Consumer Staples", "Food Products")
    parent.price0 = 120.0
    split = us[8]
    force(split, "Information Technology", "Software")
    delist = us[25]
    force(delist, "Health Care", "Services")
    winners = [us[i] for i in (17, 22, 31, 44)]
    for winner in winners:
        force(winner, "Industrials", "Machinery")
        winner.alpha = 0.0012
    tobacco = [us[i] for i in (14, 38, 57, 90, 140, 210)]
    for security in tobacco:
        force(security, "Consumer Staples", "Tobacco")
    spinco = Security(
        sid="US0301",
        name="Halvern Leaf Corp",
        sector="Consumer Staples",
        industry="Tobacco",
        country="US",
        currency="USD",
        mcap=parent.mcap * 0.05,
        beta=0.9,
        sigma=0.014,
        price0=30.0,
    )
    universe.append(spinco)
    return Plan(
        parent.sid,
        split.sid,
        delist.sid,
        [w.sid for w in winners],
        [t.sid for t in tobacco],
        spinco.sid,
    )


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the Spearman rank correlation of two equal-length sequences."""
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = math.fsum(ra) / len(ra), math.fsum(rb) / len(rb)
    cov = math.fsum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = math.fsum((x - ma) ** 2 for x in ra)
    vb = math.fsum((y - mb) ** 2 for y in rb)
    return cov / math.sqrt(va * vb)


def sample_std(values: Sequence[float]) -> float:
    """Return the sample standard deviation (n - 1)."""
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


class World:
    """The simulated market, books, and vendor files for one seed."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.dates = [d.isoformat() for d in business_days(N_DAYS)]
        self.index = {d: i for i, d in enumerate(self.dates)}
        self.universe = _make_universe(self.rng)
        self.plan = _assign_roles(self.universe)
        self.by_id = {s.sid: s for s in self.universe}
        self.rebalances = [
            i
            for i, d in enumerate(self.dates)
            if i == 0 or self.dates[i - 1][:7] != d[:7]
        ]
        self.delivery = [min(i + 1, N_DAYS - 1) for i in self.rebalances]
        self.fx: dict[str, list[float]] = {}
        self.fx_ret: dict[str, list[float]] = {}
        self.scores: list[dict[str, dict[str, float]]] = []  # by month: factor -> sid -> z
        self.usd_ret: dict[str, list[float | None]] = {}
        self._simulate()
        self._scores()
        self._returns()
        self._benchmarks()
        self._books()

    # ---- market ----------------------------------------------------------------------------

    def _simulate(self) -> None:
        rng = self.rng
        first_spin = self.index[SPIN_DATE]
        for ccy, level, drift in (("EUR", 1.08, 0.0), ("GBP", 1.27, 0.0), ("JPY", 0.0067, -0.0002)):
            rets = [0.0] + [rng.gauss(drift, 0.004) for _ in range(N_DAYS - 1)]
            levels = [level]
            for r in rets[1:]:
                levels.append(levels[-1] * (1 + r))
            self.fx[ccy], self.fx_ret[ccy] = levels, rets
        self.fx["USD"], self.fx_ret["USD"] = [1.0] * N_DAYS, [0.0] * N_DAYS
        market = [0.0] + [rng.gauss(0.0004, 0.008) for _ in range(N_DAYS - 1)]
        sector_f = {s: [rng.gauss(0, 0.005) for _ in range(N_DAYS)] for s in SECTORS}
        region_f = {c: [rng.gauss(0, 0.004) for _ in range(N_DAYS)] for c in ("DE", "FR", "GB", "JP")}
        self._market, self._sector_f, self._region_f = market, sector_f, region_f
        # Scores first, because the planted quality effect uses them: built in _scores().
        self._draws = {s.sid: [rng.gauss(0, 1) for _ in range(N_DAYS)] for s in self.universe}
        self._first_spin = first_spin

    def _scores(self) -> None:
        rng = self.rng
        names = [s.sid for s in self.universe if s.sid != self.plan.spinco]
        state: dict[str, dict[str, float]] = {f: {n: rng.gauss(0, 1) for n in names} for f in FACTORS}
        sigma_rank = _ranks([self.by_id[n].sigma for n in names])
        for _month in self.rebalances:
            month: dict[str, dict[str, float]] = {}
            for factor in FACTORS:
                for n in names:
                    state[factor][n] = 0.8 * state[factor][n] + 0.6 * rng.gauss(0, 1)
                values = state[factor]
                if factor == "lowvol":
                    for k, n in enumerate(names):
                        z = -(sigma_rank[k] - len(names) / 2) / (len(names) / 3.5)
                        values[n] = 0.7 * z + 0.7 * rng.gauss(0, 1)
                mean = math.fsum(values.values()) / len(values)
                sd = sample_std(list(values.values()))
                month[factor] = {n: (v - mean) / sd for n, v in values.items()}
            self.scores.append(month)

    def _month_of(self, day: int) -> int | None:
        """Return the index of the latest score delivery on or before ``day``, or None."""
        found = None
        for m, delivered in enumerate(self.delivery):
            if delivered <= day:
                found = m
        return found

    def _returns(self) -> None:
        """Simulate each security's vendor return and raw close, and its USD return."""
        plan = self.plan
        spin_day, split_day = self.index[SPIN_DATE], self.index[SPLIT_DATE]
        delist_day, announce = self.index[DELIST_DATE], self.index[DELIST_ANNOUNCE]
        holidays = {self.index[h] for h in JP_HOLIDAYS if h in self.index}
        rng = random.Random(self.seed + 1)
        for s in self.universe:
            s.first = spin_day if s.sid == plan.spinco else 0
            s.last = delist_day if s.sid == plan.delist else None
            true: list[float] = [0.0] * N_DAYS
            for d in range(1, N_DAYS):
                if d <= s.first:
                    continue
                region = self._region_f.get(s.country)
                factor = s.beta * self._market[d] + self._sector_f[s.sector][d]
                factor += region[d] if region else 0.0
                alpha = s.alpha
                month = self._month_of(d)
                if month is not None and s.sid in self.scores[month]["quality"]:
                    alpha += 0.0006 * self.scores[month]["quality"][s.sid]
                shock = s.sigma * self._draws[s.sid][d]
                true[d] = max(factor + alpha + shock, -0.5)
            if s.sid == plan.delist:
                true[announce] = 0.15
                true[delist_day] = 0.001
            self._shape(s, true, holidays, spin_day, split_day, rng)

    def _shape(
        self,
        s: Security,
        true: list[float],
        holidays: set[int],
        spin_day: int,
        split_day: int,
        rng: random.Random,
    ) -> None:
        plan = self.plan
        s.rets, s.close, s.volume = [], [], []
        price = s.price0
        pending = 1.0  # compounded growth of closed-market days
        shares_traded = s.mcap / s.price0 * 0.004
        for d in range(N_DAYS):
            alive = d >= s.first and (s.last is None or d <= s.last)
            if not alive:
                s.rets.append(None)
                s.close.append(None)
                s.volume.append(None)
                continue
            if d == s.first:
                vendor: float | None = None
            elif s.country == "JP" and d in holidays:
                pending *= 1 + true[d]
                vendor = None
            else:
                vendor = pending * (1 + true[d]) - 1
                pending = 1.0
            if d > s.first and not (s.country == "JP" and d in holidays):
                assert vendor is not None
                price *= 1 + vendor
            if s.sid == plan.parent and d == spin_day:
                price -= (1 / SPIN_RATIO) * self.by_id[plan.spinco].price0
            raw = price / 2.0 if s.sid == plan.split and d >= split_day else price
            if s.country == "JP" and d in holidays and vendor is None and d > s.first:
                s.close.append(s.close[-1])
                s.rets.append(None)
                s.volume.append(None)  # no row on a holiday; filled below
                continue
            s.rets.append(vendor)
            s.close.append(raw)
            s.volume.append(int(shares_traded * math.exp(rng.gauss(0, 0.3))))
        # A holiday has no row: close is carried for valuation only, in ``_value_close``.
        s.rets = [r for r in s.rets]
        self._held_rows(s, holidays)

    def _held_rows(self, s: Security, holidays: set[int]) -> None:
        """Mark holiday days as having no row: volume None and a separate flag on close."""
        s.closed = {d for d in holidays if s.country == "JP" and s.first < d}  # type: ignore[attr-defined]

    def has_row(self, s: Security, d: int) -> bool:
        """Return whether the vendor has a price row for ``s`` on day ``d``."""
        alive = d >= s.first and (s.last is None or d <= s.last)
        return alive and d not in getattr(s, "closed", set())

    def usd_price(self, s: Security, d: int) -> float | None:
        """Return the raw USD price used to value a holding, carried on closed days."""
        close = s.close[d]
        if close is None:
            return None
        return close * self.fx[s.currency][d]

    def _usd_returns(self) -> None:
        for s in self.universe:
            out: list[float | None] = [None] * N_DAYS
            for d in range(1, N_DAYS):
                alive = d > s.first and (s.last is None or d <= s.last)
                if alive:
                    local = s.rets[d] or 0.0
                    out[d] = (1 + local) * (1 + self.fx_ret[s.currency][d]) - 1
            self.usd_ret[s.sid] = out

    # ---- benchmarks --------------------------------------------------------------------------

    def _benchmarks(self) -> None:
        self._usd_returns()
        us = [s for s in self.universe if s.country == "US" and s.sid != self.plan.spinco]
        top100 = {s.sid for s in us[:100]}
        self.members: dict[str, list[str]] = {
            "BM_US300": [s.sid for s in us],
            "BM_GLOBAL200": sorted(top100 | {s.sid for s in self.universe if s.country != "US"}),
        }
        self.bench_w: dict[str, list[dict[str, float]]] = {}
        self.bench_ret: dict[str, list[float | None]] = {}
        cum: dict[str, float] = {s.sid: s.mcap for s in self.universe}
        caps: list[dict[str, float]] = []
        for d in range(N_DAYS):
            if d > 0:
                for s in self.universe:
                    r = self.usd_ret[s.sid][d]
                    if r is not None:
                        cum[s.sid] *= 1 + r
            caps.append(dict(cum))
        for bench, names in self.members.items():
            weights: list[dict[str, float]] = []
            returns: list[float | None] = [None]
            current: dict[str, float] = {}
            for d in range(N_DAYS):
                if d > 0:
                    r_d = {n: self.usd_ret[n][d] for n in current}
                    total = math.fsum(w * (r_d[n] or 0.0) for n, w in current.items())
                    returns.append(total)
                    grown = {n: w * (1 + (r_d[n] or 0.0)) for n, w in current.items()}
                    gone = {n for n in current if self.by_id[n].last == d}
                    kept = {n: w for n, w in grown.items() if n not in gone}
                    norm = math.fsum(kept.values())
                    current = {n: w / norm for n, w in kept.items()}
                if d in self.rebalances:
                    eligible = [
                        n
                        for n in names + ([self.plan.spinco] if bench == "BM_US300" and d > self.index[SPIN_DATE] else [])
                        if self.by_id[n].last is None or self.by_id[n].last > d  # type: ignore[operator]
                    ]
                    cap_sum = math.fsum(caps[d][n] for n in eligible)
                    current = {n: caps[d][n] / cap_sum for n in eligible}
                weights.append(dict(current))
            self.bench_w[bench] = weights
            self.bench_ret[bench] = returns

    # ---- books -----------------------------------------------------------------------------

    def _target(self, model: str, day: int) -> dict[str, float]:
        bench = "BM_US300" if model == "LCC" else "BM_GLOBAL200"
        bw = self.bench_w[bench][day]
        month = self._month_of(day)
        z: dict[str, float] = {}
        for n in bw:
            if month is None or n not in self.scores[month]["quality"]:
                z[n] = 0.0
                continue
            sc = self.scores[month]
            if model == "LCC":
                z[n] = 0.5 * sc["quality"][n] + 0.3 * sc["value"][n] + 0.2 * sc["momentum"][n]
            else:
                z[n] = 0.7 * sc["quality"][n] + 0.3 * sc["lowvol"][n]
        raw = {n: w * math.exp(0.6 * z[n]) for n, w in bw.items()}
        chosen = sorted(raw, key=lambda n: -raw[n])[: HOLDINGS[model]]
        plan = self.plan
        forced = [plan.parent] + ([plan.split, plan.delist, *plan.winners] if model == "LCC" else [])
        weights = {n: raw[n] for n in chosen}
        for n in forced:
            if n in bw:
                weights[n] = max(weights.get(n, 0.0), bw[n] * (2.5 if n in plan.winners else 1.0))
        if model == "LCC":
            for n in plan.tobacco[:2]:
                weights[n] = max(weights.get(n, 0.0), bw[n])
        for _ in range(3):
            total = math.fsum(weights.values())
            weights = {n: min(w / total * (1 - CASH_TARGET), CAP[model]) for n, w in weights.items()}
        total = math.fsum(weights.values())
        return {n: w / total * (1 - CASH_TARGET) for n, w in weights.items()}

    def _books(self) -> None:
        self.holdings: dict[str, list[dict[str, float]]] = {}
        self.trades: list[tuple[str, str, str, str, float, float, str, float]] = []
        self.corp: list[tuple[str, str, str, float, str | None, str]] = []
        self.values: dict[str, list[float]] = {}
        self.acct_ret: dict[str, list[float | None]] = {}
        targets = {
            (model, d): self._target(model, d) for model in ("LCC", "GQ") for d in self.rebalances
        }
        for account, _name, _strategy, _bench, _client, aum in ACCOUNTS:
            self._run_account(account, aum, targets)
        self.corp.append((self.plan.split, SPLIT_DATE, "SPLIT", 2.0, None, "2-for-1 split"))
        self.corp.append(
            (self.plan.parent, SPIN_DATE, "SPINOFF", 1 / SPIN_RATIO, self.plan.spinco, "1 for 4")
        )
        self.corp.append(
            (self.plan.delist, DELIST_DATE, "CASH_MERGER", 0.0, None, "acquired for cash")
        )

    def _excluded(self, account: str) -> set[str]:
        banned: set[str] = set()
        for acc, rule, value in RESTRICTIONS:
            if acc == account and rule == "exclude_industry":
                banned |= {s.sid for s in self.universe if s.industry == value}
        return banned

    def _run_account(
        self, account: str, aum: float, targets: dict[tuple[str, int], dict[str, float]]
    ) -> None:
        model = MODEL_OF[account]
        banned = self._excluded(account)
        shares: dict[str, float] = {}
        cash = aum
        spin_day, split_day = self.index[SPIN_DATE], self.index[SPLIT_DATE]
        sale_day, delist_day = self.index[SPIN_SALE_DATE], self.index[DELIST_DATE]
        snapshots: list[dict[str, float]] = []
        values: list[float] = []
        for d in range(N_DAYS):
            for n in list(shares):  # carried closes for valuation
                s = self.by_id[n]
                if s.last is not None and d > s.last:
                    del shares[n]
            if d == split_day and self.plan.split in shares:
                shares[self.plan.split] *= 2
            if d == spin_day and self.plan.parent in shares:
                held = shares[self.plan.parent]
                whole = math.floor(held / SPIN_RATIO)
                spin = self.by_id[self.plan.spinco]
                spin_price = spin.close[d]
                assert spin_price is not None
                shares[self.plan.spinco] = float(whole)
                cash += (held / SPIN_RATIO - whole) * spin_price  # cash in lieu of a fraction
            if d == delist_day and self.plan.delist in shares:
                s = self.by_id[self.plan.delist]
                cash += shares.pop(self.plan.delist) * (self.usd_price(s, d) or 0.0)
            if d == sale_day and self.plan.spinco in shares:
                s = self.by_id[self.plan.spinco]
                sold = shares.pop(self.plan.spinco)
                px = self.usd_price(s, d) or 0.0
                cash += sold * px
                self.trades.append((SPIN_SALE_DATE, account, s.sid, "SELL", sold, s.close[d] or 0.0, "USD", sold * px * 1e-4))
            value = cash + math.fsum(
                sh * (self.usd_price(self.by_id[n], d) or 0.0) for n, sh in shares.items()
            )
            if d in self.rebalances:
                target = {n: w for n, w in targets[model, d].items() if n not in banned}
                scale = (1 - CASH_TARGET) / math.fsum(target.values())
                new: dict[str, float] = {}
                for n, w in target.items():
                    price = self.usd_price(self.by_id[n], d)
                    assert price is not None
                    new[n] = float(round(w * scale * value / price))
                for n in sorted(set(new) | set(shares)):
                    change = new.get(n, 0.0) - shares.get(n, 0.0)
                    if change != 0:
                        s = self.by_id[n]
                        px_usd = self.usd_price(s, d) or 0.0
                        self.trades.append(
                            (self.dates[d], account, n, "BUY" if change > 0 else "SELL", abs(change),
                             s.close[d] or 0.0, s.currency, abs(change) * px_usd * 1e-4)
                        )
                shares = {n: sh for n, sh in new.items() if sh > 0}
                cash = value - math.fsum(
                    sh * (self.usd_price(self.by_id[n], d) or 0.0) for n, sh in shares.items()
                )
            snap = dict(shares)
            snap[CASH] = cash
            snapshots.append(snap)
            values.append(value)
        self.holdings[account] = snapshots
        self.values[account] = values
        gross: list[float | None] = [None]
        for d in range(1, N_DAYS):
            gross.append(values[d] / values[d - 1] - 1)
        self.acct_ret[account] = gross

    def weights(self, account: str, day: int) -> dict[str, float]:
        """Return the account's end-of-day weights on ``day``, cash included."""
        snap = self.holdings[account][day]
        out = {CASH: snap[CASH]}
        for n, sh in snap.items():
            if n != CASH:
                out[n] = sh * (self.usd_price(self.by_id[n], day) or 0.0)
        total = math.fsum(out.values())
        return {n: v / total for n, v in out.items()}

    # ---- planted answers, by code that shares nothing with the tools -------------------------

    def period_return(self, name: str, start: int, end: int) -> float:
        """Compound a security's USD return over days start+1..end; cash is zero."""
        if name == CASH:
            return 0.0
        growth = 1.0
        for d in range(start + 1, end + 1):
            r = self.usd_ret[name][d]
            growth *= 1 + (r or 0.0)
        return growth - 1

    def sector_of(self, name: str) -> str:
        """Return a security's sector, or 'Cash'."""
        return "Cash" if name == CASH else self.by_id[name].sector

    def brinson(self, account: str, bench: str, start: int, end: int) -> dict[str, Any]:
        """Brinson-Fachler over one buy-and-hold period, start weights, by sector."""
        wp, wb = self.weights(account, start), self.bench_w[bench][start]
        names = set(wp) | set(wb)
        ret = {n: self.period_return(n, start, end) for n in names}
        rp = math.fsum(wp.get(n, 0.0) * ret[n] for n in names)
        rb = math.fsum(wb.get(n, 0.0) * ret[n] for n in names)
        out: dict[str, dict[str, float]] = {}
        for sector in sorted({self.sector_of(n) for n in names}):
            members = [n for n in names if self.sector_of(n) == sector]
            p_w = math.fsum(wp.get(n, 0.0) for n in members)
            b_w = math.fsum(wb.get(n, 0.0) for n in members)
            b_r = math.fsum(wb.get(n, 0.0) * ret[n] for n in members) / b_w if b_w else rb
            p_r = math.fsum(wp.get(n, 0.0) * ret[n] for n in members) / p_w if p_w else b_r
            out[sector] = {
                "allocation": (p_w - b_w) * (b_r - rb),
                "selection": b_w * (p_r - b_r),
                "interaction": (p_w - b_w) * (p_r - b_r),
            }
        return {"portfolio_return": rp, "benchmark_return": rb, "effects": out}

    def linked(self, account: str, bench: str, edges: list[int]) -> dict[str, Any]:
        """Carino-link the single-period effects over consecutive ``edges``."""
        periods = [self.brinson(account, bench, a, b) for a, b in zip(edges, edges[1:], strict=False)]
        def k(rp: float, rb: float) -> float:
            if abs(rp - rb) < 1e-12:
                return 1 / (1 + rp)
            return (math.log(1 + rp) - math.log(1 + rb)) / (rp - rb)
        cum_p = math.prod(1 + p["portfolio_return"] for p in periods) - 1
        cum_b = math.prod(1 + p["benchmark_return"] for p in periods) - 1
        big_k = k(cum_p, cum_b)
        sectors = sorted({s for p in periods for s in p["effects"]})
        out: dict[str, dict[str, float]] = {s: {"allocation": 0.0, "selection": 0.0, "interaction": 0.0} for s in sectors}
        for p in periods:
            scale = k(p["portfolio_return"], p["benchmark_return"]) / big_k
            for sector, effects in p["effects"].items():
                for kind, value in effects.items():
                    out[sector][kind] += value * scale
        return {"portfolio_return": cum_p, "benchmark_return": cum_b, "effects": out}

    def active_share(self, account: str, bench: str, day: int) -> float:
        """Return half the sum of absolute active weights over the union of names, cash included."""
        wp, wb = self.weights(account, day), self.bench_w[bench][day]
        return 0.5 * math.fsum(abs(wp.get(n, 0.0) - wb.get(n, 0.0)) for n in set(wp) | set(wb))

    def tracking_error_ex_post(self, account: str, bench: str, end: int) -> float:
        """Annualised sample std of daily gross returns minus benchmark returns, days 1..end."""
        diffs = [
            (self.acct_ret[account][d] or 0.0) - (self.bench_ret[bench][d] or 0.0)
            for d in range(1, end + 1)
        ]
        return sample_std(diffs) * math.sqrt(DAYS_PER_YEAR)

    def tracking_error_ex_ante(self, account: str, bench: str, end: int, window: int) -> float:
        """Std of (current active weights . daily USD returns) over the last ``window`` days."""
        wp, wb = self.weights(account, end), self.bench_w[bench][end]
        active = {n: wp.get(n, 0.0) - wb.get(n, 0.0) for n in set(wp) | set(wb)}
        series = [
            math.fsum(w * (self.usd_ret[n][d] or 0.0) for n, w in active.items() if n != CASH)
            for d in range(end - window + 1, end + 1)
        ]
        return sample_std(series) * math.sqrt(DAYS_PER_YEAR)

    def adv_usd(self, name: str, day: int) -> float:
        """Mean USD traded value over the rows among the 20 business days ending ``day``."""
        s = self.by_id[name]
        values = [
            (s.volume[d] or 0) * (s.close[d] or 0.0) * self.fx[s.currency][d]
            for d in range(max(0, day - 19), day + 1)
            if self.has_row(s, d)
        ]
        return math.fsum(values) / len(values)

    def days_to_liquidate(self, account: str, day: int, aum: float, rate: float) -> dict[str, Any]:
        """Return days to sell each holding at ``rate`` of ADV, and the liquidated shares of value."""
        weights = self.weights(account, day)
        days = {
            n: w * aum / (rate * self.adv_usd(n, day)) for n, w in weights.items() if n != CASH
        }
        stock = math.fsum(w for n, w in weights.items() if n != CASH)
        profile = {
            str(k): math.fsum(min(w, k * w / days[n]) for n, w in weights.items() if n != CASH)
            / stock
            for k in (1, 5, 10, 20)
        }
        return {"max_days": max(days.values()), "stock_weight": stock, "liquidated_by_day": profile}

    def rank_ic(self, factor: str, lag: int = 0) -> float:
        """Mean cross-sectional Spearman of the latest delivered score with the next return."""
        values: list[float] = []
        for d in range(N_DAYS - 1 - lag):
            month = self._month_of(d)
            if month is None:
                continue
            names = [
                n
                for n in self.scores[month][factor]
                if self.usd_ret[n][d + 1 + lag] is not None
            ]
            if len(names) < 10:
                continue
            values.append(
                spearman(
                    [self.scores[month][factor][n] for n in names],
                    [self.usd_ret[n][d + 1 + lag] or 0.0 for n in names],
                )
            )
        return math.fsum(values) / len(values)

    def breaches(self, account: str) -> list[dict[str, Any]]:
        """Return days a held security is excluded by the account's mandate, or over its cap."""
        banned = self._excluded(account)
        caps = [float(v) for a, r, v in RESTRICTIONS if a == account and r == "max_position_weight"]
        out: list[dict[str, Any]] = []
        for d in range(N_DAYS):
            for n, w in self.weights(account, d).items():
                if n in banned and w > 0:
                    out.append({"date": self.dates[d], "security_id": n, "kind": "excluded", "weight": w})
                if caps and n != CASH and w > caps[0] + 1e-12:
                    out.append({"date": self.dates[d], "security_id": n, "kind": "max_position", "weight": w})
        return out

    def known_answers(self) -> dict[str, Any]:
        """Collect the planted truths and the conventions they are stated in."""
        end = self.index[KNOWN_DATE]
        edges = [*self.rebalances, self.rebalances[-1]][:6]
        may = self.rebalances[4], self.rebalances[5]
        lcc_may = self.brinson("LCC", "BM_US300", *may)
        lcc_linked = self.linked("LCC", "BM_US300", self.rebalances)
        flat = {
            (s, k): v for s, e in lcc_linked["effects"].items() for k, v in e.items()
        }
        biggest = max(flat, key=lambda key: abs(flat[key]))
        return {
            "seed": self.seed,
            "known_date": KNOWN_DATE,
            "securities": len(self.universe),
            "rebalance_dates": [self.dates[i] for i in self.rebalances],
            "plan": {
                "spin_parent": self.plan.parent,
                "spinco": self.plan.spinco,
                "split": self.plan.split,
                "delisted": self.plan.delist,
                "industrials_winners": self.plan.winners,
                "tobacco": self.plan.tobacco,
            },
            "conventions": {
                "active_share": "0.5 * sum |w_p - w_b| over the union of names, end-of-day weights, cash a name with benchmark weight 0",
                "tracking_error_ex_post": "sample std (n-1) of daily gross return minus benchmark return, days 1..known date, times sqrt(252)",
                "tracking_error_ex_ante": "sample std of the current active weights times the last 60 days' USD returns, times sqrt(252); cash excluded",
                "brinson": "Brinson-Fachler by sector, buy-and-hold from the start day's end-of-day weights; cash is its own sector; a sector with no benchmark weight takes the total benchmark return as its benchmark return; one with no portfolio weight takes its benchmark return",
                "linking": "Carino, over the monthly periods between rebalance dates",
                "rank_ic": "mean over days of the Spearman of the latest delivered score on day t with the USD return of day t+1 (lag 0, horizon 1)",
            },
            "active_share": {
                "LCC": self.active_share("LCC", "BM_US300", end),
                "GQ": self.active_share("GQ", "BM_GLOBAL200", end),
            },
            "tracking_error_ex_post": {
                "LCC": self.tracking_error_ex_post("LCC", "BM_US300", end),
                "GQ": self.tracking_error_ex_post("GQ", "BM_GLOBAL200", end),
            },
            "tracking_error_ex_ante_60d": {
                "LCC": self.tracking_error_ex_ante("LCC", "BM_US300", end, 60),
                "GQ": self.tracking_error_ex_ante("GQ", "BM_GLOBAL200", end, 60),
            },
            "attribution_lcc_may": {
                "start": self.dates[may[0]],
                "end": self.dates[may[1]],
                **lcc_may,
            },
            "attribution_lcc_linked_monthly": lcc_linked,
            "largest_linked_effect": {
                "sector": biggest[0],
                "kind": biggest[1],
                "value": flat[biggest],
            },
            "currency_gq_may": self.currency_effect("GQ", "BM_GLOBAL200", *may),
            "days_to_liquidate_lcc": self.days_to_liquidate("LCC", end, 1.5e9, 0.2),
            "rank_ic": {f: self.rank_ic(f) for f in FACTORS},
            "breaches": {a: self.breaches(a) for a in ("SMA_FND_B", "SMA_SWF_C", "SMA_PEN_A", "LCC")},
            "last_edges_unused": edges[-1],
        }

    def currency_effect(self, account: str, bench: str, start: int, end: int) -> dict[str, float]:
        """Local-market active return, currency allocation effect, and the residual."""
        wp, wb = self.weights(account, start), self.bench_w[bench][start]
        names = set(wp) | set(wb)

        def ccy(n: str) -> str:
            return "USD" if n == CASH else self.by_id[n].currency

        def local(n: str) -> float:
            if n == CASH:
                return 0.0
            g = 1.0
            for d in range(start + 1, end + 1):
                if start < d and (self.by_id[n].last is None or d <= self.by_id[n].last):  # type: ignore[operator]
                    g *= 1 + (self.by_id[n].rets[d] or 0.0)
            return g - 1

        total = math.fsum(
            (wp.get(n, 0.0) - wb.get(n, 0.0)) * self.period_return(n, start, end) for n in names
        )
        local_active = math.fsum((wp.get(n, 0.0) - wb.get(n, 0.0)) * local(n) for n in names)
        fx_return: dict[str, float] = {}
        for c in ("USD", "EUR", "GBP", "JPY"):
            g = 1.0
            for d in range(start + 1, end + 1):
                g *= 1 + self.fx_ret[c][d]
            fx_return[c] = g - 1
        e_b = math.fsum(
            math.fsum(wb.get(n, 0.0) for n in names if ccy(n) == c) * fx_return[c]
            for c in fx_return
        )
        alloc = math.fsum(
            (
                math.fsum(wp.get(n, 0.0) for n in names if ccy(n) == c)
                - math.fsum(wb.get(n, 0.0) for n in names if ccy(n) == c)
            )
            * (fx_return[c] - e_b)
            for c in fx_return
        )
        return {
            "active_return_usd": total,
            "active_return_local": local_active,
            "currency_allocation": alloc,
            "residual": total - local_active - alloc,
        }


# ---- writing ---------------------------------------------------------------------------------


def _write_parquet(path: Path, columns: dict[str, list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path, compression="zstd")


def _curate_raw_to_lake(world: World, out: Path) -> None:
    """Write the vendor files as landed, then run the Glue job's stand-in over them."""
    raw = out / BUCKETS["s3://corvane-raw"] / "raw"
    curated = out / BUCKETS["s3://corvane-curated"] / "curated"
    for m, day in enumerate(world.rebalances):
        stamp = world.dates[day].replace("-", "")
        path = raw / "vendor-benchmark" / f"constituents_{stamp}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["benchmark_id", "security_id", "weight_pct"])
            for bench, weights in world.bench_w.items():
                for sid, w in sorted(weights[day].items()):
                    writer.writerow([bench, sid, f"{w * 100:.8f}"])
        delivered = world.dates[world.delivery[m]]
        path = raw / "vendor-factors" / f"scores_{delivered.replace('-', '')}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["as_of", "security_id", *FACTORS])
            ids = sorted(world.scores[m]["quality"])
            for sid in ids:
                writer.writerow([world.dates[day], sid, *(f"{world.scores[m][f][sid]!r}" for f in FACTORS)])
    # Glue job stand-in: wide vendor scores to long curated Parquet, partitioned by delivery date.
    for path in sorted((raw / "vendor-factors").glob("scores_*.csv")):
        dt = f"{path.stem[7:11]}-{path.stem[11:13]}-{path.stem[13:15]}"
        ids: list[str] = []
        as_of: list[str] = []
        factor_ids: list[str] = []
        scores: list[float] = []
        with path.open() as handle:
            for row in csv.DictReader(handle):
                for factor in FACTORS:
                    ids.append(row["security_id"])
                    as_of.append(row["as_of"])
                    factor_ids.append(factor)
                    scores.append(float(row[factor]))
        _write_parquet(
            curated / "factor_scores" / f"dt={dt}" / "part-0000.parquet",
            {"security_id": ids, "factor_id": factor_ids, "score": scores, "as_of": as_of},
        )
    for d, day in enumerate(world.dates):
        rows = [
            (s, d) for s in world.universe if world.has_row(s, d)
        ]
        _write_parquet(
            curated / "prices_daily" / f"dt={day}" / "part-0000.parquet",
            {
                "security_id": [s.sid for s, _ in rows],
                "close_local": [s.close[d] for s, _ in rows],
                "total_return_local": [s.rets[d] for s, _ in rows],
                "volume": [s.volume[d] for s, _ in rows],
            },
        )
        _write_parquet(
            curated / "fx_daily" / f"dt={day}" / "part-0000.parquet",
            {
                "currency": ["EUR", "GBP", "JPY"],
                "usd_per_unit": [world.fx[c][d] for c in ("EUR", "GBP", "JPY")],
            },
        )


def _snowflake(world: World, out: Path) -> None:
    folder = out / "snowflake"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("CORVANE_CURATED.db", "CORVANE_REF.db"):
        (folder / name).unlink(missing_ok=True)
    with closing(sqlite3.connect(folder / "CORVANE_CURATED.db")) as db, db:
        db.executescript(
            """
            CREATE TABLE SECURITY_MASTER (SECURITY_ID TEXT PRIMARY KEY, NAME TEXT, SECTOR TEXT,
              INDUSTRY TEXT, COUNTRY TEXT, CURRENCY TEXT, MCAP_USD REAL);
            CREATE TABLE BENCH_WEIGHTS (AS_OF_DATE TEXT, BENCHMARK_ID TEXT, SECURITY_ID TEXT,
              WEIGHT REAL);
            CREATE TABLE BENCH_RETURNS (DATE TEXT, BENCHMARK_ID TEXT, TOTAL_RETURN_USD REAL);
            CREATE TABLE HOLDINGS_DAILY (AS_OF_DATE TEXT, ACCOUNT_ID TEXT, SECURITY_ID TEXT,
              SHARES REAL);
            CREATE TABLE TRADES (TRADE_DATE TEXT, ACCOUNT_ID TEXT, SECURITY_ID TEXT, SIDE TEXT,
              SHARES REAL, PRICE_LOCAL REAL, CURRENCY TEXT, COMMISSION_USD REAL);
            CREATE TABLE CORP_ACTIONS (SECURITY_ID TEXT, EX_DATE TEXT, ACTION_TYPE TEXT,
              RATIO REAL, NEW_SECURITY_ID TEXT, NOTE TEXT);
            CREATE TABLE ACCT_RETURNS (DATE TEXT, ACCOUNT_ID TEXT, GROSS_RETURN REAL,
              NET_RETURN REAL);
            """
        )
        masters = [
            (s.sid, s.name, s.sector, s.industry, s.country, s.currency, s.mcap)
            for s in world.universe
        ]
        masters.append((CASH, "US dollar cash", "Cash", "Cash", "US", "USD", 0.0))
        db.executemany("INSERT INTO SECURITY_MASTER VALUES (?,?,?,?,?,?,?)", masters)
        db.executemany(
            "INSERT INTO BENCH_WEIGHTS VALUES (?,?,?,?)",
            [
                (world.dates[day], bench, sid, w)
                for bench, weights in world.bench_w.items()
                for day in world.rebalances
                for sid, w in sorted(weights[day].items())
            ],
        )
        db.executemany(
            "INSERT INTO BENCH_RETURNS VALUES (?,?,?)",
            [
                (world.dates[d], bench, r)
                for bench, rets in world.bench_ret.items()
                for d, r in enumerate(rets)
            ],
        )
        db.executemany(
            "INSERT INTO HOLDINGS_DAILY VALUES (?,?,?,?)",
            [
                (world.dates[d], account, sid, sh)
                for account, snaps in world.holdings.items()
                for d, snap in enumerate(snaps)
                for sid, sh in sorted(snap.items())
            ],
        )
        db.executemany("INSERT INTO TRADES VALUES (?,?,?,?,?,?,?,?)", world.trades)
        db.executemany("INSERT INTO CORP_ACTIONS VALUES (?,?,?,?,?,?)", world.corp)
        net: list[tuple[str, str, float | None, float | None]] = []
        for account, gross in world.acct_ret.items():
            for d, g in enumerate(gross):
                net.append(
                    (world.dates[d], account, g, None if g is None else g - MANAGEMENT_FEE / DAYS_PER_YEAR)
                )
        db.executemany("INSERT INTO ACCT_RETURNS VALUES (?,?,?,?)", net)
    with closing(sqlite3.connect(folder / "CORVANE_REF.db")) as db, db:
        db.executescript(
            """
            CREATE TABLE ACCOUNTS (ACCOUNT_ID TEXT PRIMARY KEY, NAME TEXT, STRATEGY TEXT,
              BENCHMARK_ID TEXT, CLIENT TEXT, AUM_USD REAL, BASE_CCY TEXT);
            CREATE TABLE MANDATE_RESTRICTIONS (ACCOUNT_ID TEXT, RULE_TYPE TEXT, RULE_VALUE TEXT);
            """
        )
        db.executemany(
            "INSERT INTO ACCOUNTS VALUES (?,?,?,?,?,?,'USD')", [a[:6] for a in ACCOUNTS]
        )
        db.executemany("INSERT INTO MANDATE_RESTRICTIONS VALUES (?,?,?)", RESTRICTIONS)


def _catalog(out: Path) -> None:
    columns = {
        "prices_daily": [
            ("security_id", "string"),
            ("close_local", "double"),
            ("total_return_local", "double"),
            ("volume", "bigint"),
        ],
        "fx_daily": [("currency", "string"), ("usd_per_unit", "double")],
        "factor_scores": [
            ("security_id", "string"),
            ("factor_id", "string"),
            ("score", "double"),
            ("as_of", "string"),
        ],
    }
    catalog = {
        "DatabaseName": "corvane",
        "TableList": [
            {
                "Name": table,
                "StorageDescriptor": {
                    "Columns": [{"Name": n, "Type": t} for n, t in cols],
                    "Location": f"s3://corvane-curated/curated/{table}/",
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                },
                "PartitionKeys": [{"Name": "dt", "Type": "string"}],
                "Parameters": {"classification": "parquet"},
            }
            for table, cols in columns.items()
        ],
    }
    path = out / "catalog" / "glue_catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2) + "\n")


def build(out: Path, seed: int = DEFAULT_SEED) -> World:
    """Build the whole estate under ``out`` and return the simulated world."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    world = World(seed)
    _curate_raw_to_lake(world, out)
    _snowflake(world, out)
    _catalog(out)
    research = out / BUCKETS["s3://corvane-research"] / "research"
    research.mkdir(parents=True)
    for doc in sorted(RESEARCH_DIR.glob("*.md")):
        shutil.copy(doc, research / doc.name)
    (out / "s3_map.json").write_text(json.dumps(BUCKETS, indent=2) + "\n")
    (out / "known_answers.json").write_text(json.dumps(world.known_answers(), indent=2) + "\n")
    return world


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("out", nargs="?", default="corvane-estate", type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    build(args.out, args.seed)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
