"""Larchmere's CLO compliance arithmetic: plain pandas over the datasets the store serves.

Conventions (the same as `facility/warehouse/known_answers.json` states):
- Performing: rating factor below 10000. Ca, C, and D (factor 10000) are defaulted.
- CCC: performing, rating factor 4770 (Caa1) to 9999. The excess over 7.5% of performing par is
  carried at market price, lowest priced first.
- Adjusted par = performing par - CCC excess haircut + defaulted par at market price + principal
  cash.
- OC of a class = adjusted par / balance of the class and everything senior.
- IC of a class = annual interest on performing par (floors applied, bonds at their fixed coupon) /
  annual interest on the class and everything senior at SOFR + spread. Fees are excluded.
- WARF: par-weighted rating factor of performing loans. WAS: bps, par-weighted over performing
  floating loans, spread + max(floor - SOFR, 0). WAL: years, actual days / 365.25.
- Diversity: simplified; the trustee's Moody's score is about 2 to 2.5 points higher.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

DEFAULT_RF = 10000.0
CCC_RF = 4770.0
CCC_LIMIT = 0.075
DS_TABLE = [0.0, 1.0, 1.5, 2.0, 2.33, 2.67, 3.0, 3.25, 3.5, 3.75, 4.0]
OC_CLASSES = {
    "oc_ab": ("AAA", "AA"), "oc_c": ("AAA", "AA", "A"),
    "oc_d": ("AAA", "AA", "A", "BBB"), "oc_e": ("AAA", "AA", "A", "BBB", "BB"),
}  # fmt: skip
IC_CLASSES = {name.replace("oc", "ic"): classes for name, classes in OC_CLASSES.items()}
TRIGGER_TRANCHE = {"ab": "AAA", "c": "A", "d": "BBB", "e": "BB"}
MIN_TESTS = ("oc_ab", "oc_c", "oc_d", "oc_e", "ic_ab", "ic_c", "ic_d", "ic_e", "was", "diversity")
MAX_TESTS = ("ccc_pct", "warf", "wal", "obligor_pct", "industry_pct", "second_lien_pct", "bond_pct")
LIMIT_TESTS = ("ccc_pct", "warf", "was", "wal", "diversity", "obligor_pct", "industry_pct",
               "second_lien_pct", "bond_pct")  # fmt: skip
FACILITY = {1: "FIRST_LIEN", 2: "SECOND_LIEN", 3: "BOND"}
UNITS = {"oc": "ratio", "ic": "ratio", "was": "bps", "wal": "years", "diversity": "points"}


@dataclass
class TestResult:
    """One compliance test on one date."""

    __test__ = False  # not a pytest class

    name: str
    value: float
    level: float
    kind: str  # "min": value must be at least level; "max": at most
    passed: bool
    headroom: float  # distance to the level in the passing direction; negative when failing


@dataclass
class ComplianceReport:
    """Every test for one deal on one report date."""

    as_of: str
    tests: list[TestResult]
    failed: list[str]
    adjusted_par: float
    ccc_haircut: float
    performing_par: float
    sofr_pct: float
    values: dict[
        str, float
    ]  # test name to value: a flat copy, because acceptance paths cannot index a list
    warnings: list[str] = field(default_factory=list)


@dataclass
class TestChange:
    """One test before and after a trade."""

    __test__ = False

    name: str
    before: float
    after: float
    delta: float
    passes_before: bool
    passes_after: bool


@dataclass
class WhatIfReport:
    """A trade's effect on every test."""

    as_of: str
    loan_id: str
    par_change: float
    price: float
    cash_after: float
    changes: list[TestChange]
    newly_failing: list[str]
    newly_passing: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class EquityReturns:
    """Cash-on-cash and IRR of an equity position."""

    invested: float
    total_distributions: float
    terminal_nav: float
    years: float
    cash_on_cash_annual: float
    irr: float
    moic: float
    warnings: list[str] = field(default_factory=list)


def level_for(name: str, structure: pd.Series) -> float:
    """Return the trigger or limit for ``name`` from a structure row."""
    if name[:2] in ("oc", "ic"):
        return float(structure[f"{name[:2]}_trig_{TRIGGER_TRANCHE[name[3:]]}"])
    return float(structure[f"limit_{name}"])


def pick_date(index: pd.Index, as_of: str, what: str) -> pd.Timestamp:
    """Return ``as_of`` as a Timestamp in ``index``, or raise naming the nearest dates."""
    stamp = pd.Timestamp(as_of)
    if stamp not in index:
        near = [d.date().isoformat() for d in index[index.get_indexer([stamp], method="nearest")]]
        raise ValueError(f"{what} has no row for {as_of}; the nearest date is {near[0]}.")
    return stamp


def sofr_on(sofr: pd.DataFrame, stamp: pd.Timestamp) -> float:
    """Return SOFR in percent on or before ``stamp``."""
    series = sofr.iloc[:, 0].dropna()
    earlier = series[series.index <= stamp]
    if earlier.empty:
        raise ValueError(f"The SOFR history starts after {stamp.date()}.")
    return float(earlier.iloc[-1])


def book_at(
    par: pd.DataFrame,
    price: pd.DataFrame,
    rating: pd.DataFrame,
    static: pd.DataFrame,
    stamp: pd.Timestamp,
) -> pd.DataFrame:
    """Return the holdings on ``stamp`` as one row per loan with everything a test needs."""
    held = par.loc[stamp].dropna()
    held = held[held > 0]
    book = pd.DataFrame({"par": held, "price": price.loc[stamp].reindex(held.index)})
    book["rf"] = rating.loc[stamp].reindex(held.index)
    return attach_terms(book, static, stamp)


def attach_terms(book: pd.DataFrame, static: pd.DataFrame, stamp: pd.Timestamp) -> pd.DataFrame:
    """Add each loan's terms from the static table."""
    terms = static.reindex(book.index)
    out = book.copy()
    out["spread_bps"] = terms["spread_bps"]
    out["floor_bps"] = terms["floor_bps"]
    out["fixed"] = terms["fixed_coupon_bps"].where(terms["is_fixed"] == 1)
    out["years"] = (
        terms["maturity_epoch_day"] - (stamp - pd.Timestamp("1970-01-01")).days
    ) / 365.25
    out["industry"] = terms["industry_num"]
    out["obligor"] = terms["obligor_num"]
    out["facility"] = terms["facility_code"]
    return out


def compute_tests(
    book: pd.DataFrame, structure: pd.Series, sofr_pct: float, cash: float
) -> dict[str, float]:
    """Return every test value for ``book`` (see the module's conventions)."""
    sofr = sofr_pct / 100
    perf = book[book["rf"] < DEFAULT_RF]
    dflt = book[book["rf"] >= DEFAULT_RF]
    perf_par = float(perf["par"].sum())
    ccc = perf[perf["rf"] >= CCC_RF].sort_values("price")
    excess = max(float(ccc["par"].sum()) - CCC_LIMIT * perf_par, 0.0)
    haircut = 0.0
    for _, row in ccc.iterrows():
        take = min(float(row["par"]), excess)
        haircut += take * (1 - float(row["price"]) / 100)
        excess -= take
    adjusted = perf_par - haircut + float((dflt["par"] * dflt["price"] / 100).sum()) + cash
    out = {"adjusted_par": adjusted, "ccc_haircut": haircut, "performing_par": perf_par}
    for name, classes in OC_CLASSES.items():
        out[name] = adjusted / sum(float(structure[f"bal_{t}"]) for t in classes)
    rate = (perf["fixed"] / 1e4).where(
        perf["fixed"].notna(), (perf["floor_bps"] / 1e4).clip(lower=sofr) + perf["spread_bps"] / 1e4
    )
    income = float((perf["par"] * rate).sum())
    for name, classes in IC_CLASSES.items():
        due = sum(
            float(structure[f"bal_{t}"]) * (sofr + float(structure[f"spread_{t}"]) / 1e4)
            for t in classes
        )
        out[name] = income / due
    out["ccc_pct"] = 100 * float(ccc["par"].sum()) / perf_par
    out["warf"] = float((perf["par"] * perf["rf"]).sum()) / perf_par
    floating = perf[perf["fixed"].isna()]
    effective = floating["spread_bps"] + (floating["floor_bps"] - sofr * 1e4).clip(lower=0)
    out["was"] = float((floating["par"] * effective).sum() / floating["par"].sum()) / 100
    out["wal"] = float((perf["par"] * perf["years"]).sum()) / perf_par
    by_obligor = perf.groupby("obligor")["par"].sum()
    industry_of = perf.groupby("obligor")["industry"].first()
    units = (by_obligor / (perf_par / len(by_obligor))).clip(upper=1.0).groupby(industry_of).sum()
    out["diversity"] = sum(_industry_score(float(u)) for u in units)
    out["obligor_pct"] = 100 * float(by_obligor.max()) / perf_par
    out["industry_pct"] = 100 * float(by_obligor.groupby(industry_of).sum().max()) / perf_par
    out["second_lien_pct"] = 100 * float(perf.loc[perf["facility"] == 2, "par"].sum()) / perf_par
    out["bond_pct"] = 100 * float(perf.loc[perf["facility"] == 3, "par"].sum()) / perf_par
    return out


def _industry_score(units: float) -> float:
    units = min(units, 10.0)
    low = int(units)
    return DS_TABLE[low] + (DS_TABLE[min(low + 1, 10)] - DS_TABLE[low]) * (units - low)


def judge(values: dict[str, float], structure: pd.Series) -> list[TestResult]:
    """Return each test against its trigger or limit."""
    results = []
    for name in (*MIN_TESTS, *MAX_TESTS):
        level = level_for(name, structure)
        kind = "min" if name in MIN_TESTS else "max"
        room = values[name] - level if kind == "min" else level - values[name]
        results.append(TestResult(name, values[name], level, kind, room >= 0, room))
    return results


def years_to_epoch(day: date) -> int:
    """Return ``day`` as days since 1970-01-01, the encoding of maturity in the static table."""
    return (day - date(1970, 1, 1)).days


def xirr(flows: list[tuple[date, float]]) -> float:
    """Return the annual IRR of dated flows by bisection (actual/365), or NaN when there is none."""
    first = flows[0][0]

    def npv(rate: float) -> float:
        return float(
            sum(amount / (1 + rate) ** ((day - first).days / 365) for day, amount in flows)
        )

    low, high = -0.99, 10.0
    if npv(low) * npv(high) > 0:
        return math.nan
    for _ in range(200):
        mid = (low + high) / 2
        low, high = (mid, high) if npv(low) * npv(mid) > 0 else (low, mid)
    return (low + high) / 2


def equity_returns(flows: pd.DataFrame) -> EquityReturns:
    """Return IRR and cash-on-cash from invested, distribution, and terminal_nav columns."""
    frame = flows.fillna(0.0)
    dated = [
        (idx.date(), float(-row["invested"] + row["distribution"] + row["terminal_nav"]))
        for idx, row in frame.iterrows()
    ]
    invested = float(frame["invested"].sum())
    paid = float(frame["distribution"].sum())
    nav = float(frame["terminal_nav"].sum())
    years = (dated[-1][0] - dated[0][0]).days / 365
    warnings = []
    if (frame["invested"] > 0).sum() != 1:
        warnings.append(
            "Expected exactly one invested amount; the IRR may not mean what you expect."
        )
    return EquityReturns(
        invested=invested, total_distributions=paid, terminal_nav=nav, years=years,
        cash_on_cash_annual=paid / invested / years, irr=xirr(dated), moic=(paid + nav) / invested,
        warnings=warnings,
    )  # fmt: skip
