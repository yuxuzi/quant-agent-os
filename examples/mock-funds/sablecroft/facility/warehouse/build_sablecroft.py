"""Sablecroft Alternatives (fictional): builds the fund's Azure estate as local stand-ins.

Azure shapes, local directories (nothing here talks to Azure or Databricks):
- An ADLS Gen2 account `sablecroftlake` with two containers. `raw` holds what administrators and
  general partners send: monthly NAV statements and quarterly capital-account statements, as files
  (`abfss://raw@sablecroftlake.dfs.core.windows.net/admin-nav/<administrator>/<yyyymm>/...`).
  `uc` is the Unity Catalog managed storage: Delta tables on ADLS, at
  `abfss://uc@sablecroftlake.dfs.core.windows.net/sablecroft/<schema>/<table>/`, each a folder of
  Parquet files (Hive-partitioned where the fund partitions) with a minimal `_delta_log/` (one
  commit of `add` actions; not a full Delta writer). `lake_map.json` says which abfss URI is which
  local directory.
- An Azure Databricks Unity Catalog `sablecroft`, schemas `curated` (returns, positions, cash flows,
  NAVs, FX, indices) and `ref` (managers, redemption terms, private-fund commitments, NAV
  reconciliation breaks). Three-level names: `sablecroft.curated.mgr_returns`. A Databricks SQL
  warehouse would read them; site/ holds the views and a local stand-in for the warehouse.
- `known_answers.json`: the planted truths, each with the convention it is stated in.

Everything is fictional and deterministic for a seed. Usage: python build_sablecroft.py OUT_DIR [--seed N]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_SEED = 20251231
ACCOUNT = "sablecroftlake"
HOST = f"{ACCOUNT}.dfs.core.windows.net"
AS_OF = date(2026, 1, 10)  # the data cut: what had been published by then
FIRST_MONTH = date(2021, 1, 31)
MONTHS = 60
EST_DAYS = 5  # an estimate arrives this many days after month-end
LIQUIDATED = "EV04"
BIASED = "EV02"  # its estimates run below its finals: revisions are biased positive
THETAS = {"IC01": (0.52, 0.30, 0.18), "RA01": (0.45, 0.33, 0.22)}  # MA(2), summing to 1
BIAS = 0.0018

# (id, name, strategy, currency, lag days, frequency, alpha/month, beta, idio sd, administrator)
MANAGERS: list[tuple[str, str, str, str, int, str, float, float, float, str]] = [
    ("EQ01", "Harbourlight Equity Partners", "eq_ls", "USD", 15, "M", 0.0030, 0.45, 0.022, "NGFS"),
    ("EQ02", "Tern Rock Capital", "eq_ls", "USD", 15, "M", 0.0020, 0.55, 0.025, "NGFS"),
    ("EQ03", "Calder Vale Long/Short", "eq_ls", "EUR", 15, "M", 0.0025, 0.40, 0.020, "NGFS"),
    ("EQ04", "Ashby Lane Partners", "eq_ls", "USD", 15, "M", 0.0010, 0.60, 0.028, "NGFS"),
    ("MC01", "Stonebridge Macro", "macro", "USD", 15, "M", 0.0030, 0.10, 0.030, "NGFS"),
    ("MC02", "Pelham Global Macro", "macro", "USD", 15, "M", 0.0020, 0.05, 0.026, "NGFS"),
    ("MC03", "Orrin Rates Fund", "macro", "EUR", 15, "M", 0.0020, 0.00, 0.020, "NGFS"),
    ("MC04", "Vantor Discretionary", "macro", "USD", 15, "M", 0.0015, 0.15, 0.032, "NGFS"),
    ("EV01", "Kestrel Moor Special Sits", "event", "USD", 45, "M", 0.0025, 0.30, 0.016, "AHA"),
    ("EV02", "Marlow Event Opportunities", "event", "USD", 45, "M", 0.0030, 0.28, 0.015, "AHA"),
    ("EV03", "Dunmore Merger Arb", "event", "EUR", 45, "M", 0.0020, 0.20, 0.010, "AHA"),
    ("EV04", "Brindle Hollow Activist", "event", "USD", 45, "M", 0.0010, 0.45, 0.030, "AHA"),
    ("RV01", "Quillon Convertible Arb", "relval", "USD", 45, "M", 0.0030, 0.12, 0.011, "AHA"),
    ("RV02", "Lathe Street Fixed Income RV", "relval", "USD", 45, "M", 0.0025, 0.08, 0.009, "AHA"),
    ("RV03", "Fenwick Vol Arb", "relval", "EUR", 45, "M", 0.0020, 0.10, 0.013, "AHA"),
    ("CT01", "Aldwych Trend", "cta", "USD", 15, "M", 0.0020, -0.02, 0.035, "NGFS"),
    ("CT02", "Rowan Systematic", "cta", "USD", 15, "M", 0.0015, 0.00, 0.030, "NGFS"),
    ("CT03", "Greywater Futures", "cta", "USD", 15, "M", 0.0010, 0.02, 0.040, "NGFS"),
    ("MS01", "Corvane Multi-Strategy", "multistrat", "USD", 45, "M", 0.0030, 0.10, 0.012, "AHA"),
    ("MS02", "Hexham Platform", "multistrat", "USD", 45, "M", 0.0028, 0.08, 0.011, "AHA"),
    ("IC01", "Wrenfield Specialty Credit", "illiquid_credit", "USD", 45, "M", 0.0050, 0.20, 0.022, "AHA"),
    ("IC02", "Lowmoor Private Credit", "illiquid_credit", "USD", 75, "Q", 0.0055, 0.15, 0.018, "AHA"),
    ("RA01", "Thornley Real Assets", "real_assets", "USD", 45, "M", 0.0040, 0.25, 0.020, "AHA"),
    ("RA02", "Bexley Property Income", "real_assets", "EUR", 60, "Q", 0.0035, 0.20, 0.016, "AHA"),
    ("RA03", "Stannard Core Real Estate", "real_assets", "USD", 60, "Q", 0.0035, 0.22, 0.015, "AHA"),
]
STRATEGIES = [
    "eq_ls", "macro", "event", "relval", "cta", "multistrat", "illiquid_credit", "real_assets"
]
# (lockup months, notice days, redemption frequency in months, gate fraction per dealing date)
TERMS: dict[str, tuple[int, int, int, float]] = {
    "eq_ls": (12, 45, 3, 0.25), "macro": (0, 30, 1, 1.0), "event": (12, 60, 3, 0.25),
    "relval": (6, 45, 3, 0.33), "cta": (0, 10, 1, 1.0), "multistrat": (12, 90, 3, 0.25),
    "illiquid_credit": (24, 90, 3, 0.10), "real_assets": (36, 90, 12, 0.10),
}
SCHEMAS = {
    "mgr_returns": "curated", "mgr_returns_q": "curated", "fof_positions": "curated",
    "fof_fund": "curated", "pm_cashflows": "curated", "pm_nav": "curated",
    "fx_rates": "curated", "idx_public": "curated", "managers": "ref", "terms": "ref",
    "pm_funds": "ref", "nav_recon": "ref",
}
CATALOG = "sablecroft"
DELTA_FILES: dict[Path, list[tuple[str, int]]] = {}
PM_FUNDS: list[tuple[str, str, str, int, str, float, str]] = [
    ("PM01", "Dovecote Buyout I", "buyout", 2021, "USD", 100e6, "Dovecote Partners"),
    ("PM02", "Marchmont Buyout II", "buyout", 2016, "USD", 120e6, "Marchmont Capital"),
    ("PM03", "Ellery Growth III", "buyout", 2018, "EUR", 90e6, "Ellery Equity"),
    ("PM04", "Pinecroft Buyout IV", "buyout", 2020, "USD", 110e6, "Pinecroft"),
    ("PM05", "Larkspur Ventures V", "venture", 2017, "USD", 60e6, "Larkspur"),
    ("PM06", "Nimbus Seed Fund", "venture", 2020, "USD", 40e6, "Nimbus VC"),
    ("PM07", "Ortega Direct Lending I", "private_credit", 2018, "USD", 80e6, "Ortega Credit"),
    ("PM08", "Weald Senior Debt II", "private_credit", 2020, "EUR", 70e6, "Weald Capital"),
    ("PM09", "Ironbridge Mezzanine", "private_credit", 2022, "USD", 75e6, "Ironbridge"),
    ("PM10", "Sandlewood Property I", "real_estate", 2016, "USD", 65e6, "Sandlewood"),
    ("PM11", "Calloway Logistics", "real_estate", 2019, "EUR", 85e6, "Calloway RE"),
    ("PM12", "Redfern Core Plus", "real_estate", 2022, "USD", 95e6, "Redfern"),
]
NAV_LAST = date(2025, 9, 30)  # the last quarter-end whose GP statements had arrived at AS_OF
INDEX_FOR = {"USD": "SCI_US", "EUR": "SCI_EU"}
PM_PARAMS = {  # quarterly growth, distribution rate of opening NAV, first distribution quarter
    "buyout": (0.030, 0.10, 12), "venture": (0.028, 0.08, 16), "private_credit": (0.020, 0.07, 6),
    "real_estate": (0.014, 0.04, 6),
}

TABLES = list(SCHEMAS)


def month_end(year: int, month: int) -> date:
    """Return the last day of a month."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def month_ends() -> list[date]:
    """Return the 60 month-ends, 2021-01 to 2025-12."""
    return [month_end(2021 + i // 12, i % 12 + 1) for i in range(MONTHS)]


def xirr(flows: list[tuple[date, float]]) -> float:
    """Annualised internal rate of return on dated flows, actual/365, found by bisection."""
    t0 = min(day for day, _ in flows)

    def npv(rate: float) -> float:
        return math.fsum(v / (1 + rate) ** ((d - t0).days / 365.0) for d, v in flows)

    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def index_on(levels: dict[date, float], day: date) -> float:
    """Return the index level on ``day``, or on the last business day before it."""
    while day not in levels:
        day -= timedelta(days=1)
    return levels[day]


def table_dir(root: Path, table: str) -> Path:
    """Return the Delta table's folder: uc/sablecroft/<schema>/<table>."""
    return root / "uc" / CATALOG / SCHEMAS[table] / table


def write_parquet(root: Path, table: str, partition: str, rows: list[dict[str, Any]]) -> None:
    """Write rows to a Parquet file of a Delta table (Hive partition folder when given)."""
    base = table_dir(root, table)
    folder = base / partition if partition else base
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "part-0000.parquet"
    pq.write_table(pa.Table.from_pylist(rows), target)
    DELTA_FILES.setdefault(base, []).append((str(target.relative_to(base)), target.stat().st_size))


def write_delta_logs() -> None:
    """Write version 0 of each table's `_delta_log`: protocol, metadata, and one `add` per file."""
    for base, files in DELTA_FILES.items():
        log = base / "_delta_log"
        log.mkdir(exist_ok=True)
        actions: list[dict[str, Any]] = [
            {"protocol": {"minReaderVersion": 1, "minWriterVersion": 2}},
            {"metaData": {"id": base.name, "format": {"provider": "parquet"},
                          "partitionColumns": [], "createdTime": 1767225600000}},
        ]
        actions += [{"add": {"path": path, "size": size, "partitionValues": {},
                             "modificationTime": 1767225600000, "dataChange": True}}
                    for path, size in sorted(files)]
        (log / "00000000000000000000.json").write_text(
            "\n".join(json.dumps(a) for a in actions) + "\n")


def generate_returns(rng: random.Random) -> dict[str, dict[date, float]]:
    """Return each monthly manager's final monthly return in its own currency, and the latent
    (unsmoothed) series of the smoothed ones through `LATENT`."""
    months = month_ends()
    market = [rng.gauss(0.006, 0.035) for _ in range(MONTHS + 2)]
    LATENT.clear()
    out: dict[str, dict[date, float]] = {}
    for mid, _n, _s, _c, _l, _f, alpha, beta, sd, _a in MANAGERS:
        latent = [alpha + beta * market[i] + sd * rng.gauss(0, 1) for i in range(MONTHS + 2)]
        theta = THETAS.get(mid)
        if theta:
            observed = [
                theta[0] * latent[i + 2] + theta[1] * latent[i + 1] + theta[2] * latent[i]
                for i in range(MONTHS)
            ]
            LATENT[mid] = latent[2:]
        else:
            observed = latent[2:]
        series = dict(zip(months, observed, strict=True))
        if mid == LIQUIDATED:  # a hard last year, then wound down at 2024-06-30
            series = {d: (v - 0.012 if d.year == 2024 else v) for d, v in series.items()}
            series = {d: v for d, v in series.items() if d <= date(2024, 6, 30)}
        out[mid] = series
    return out


LATENT: dict[str, list[float]] = {}


def build(out: Path, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Build the lake, the SQL stand-in, and the known answers under ``out``; return the answers."""
    rng = random.Random(seed)
    DELTA_FILES.clear()
    out.mkdir(parents=True, exist_ok=True)
    months = month_ends()
    finals = generate_returns(rng)
    answers: dict[str, Any] = {"seed": seed, "as_of": AS_OF.isoformat()}

    # --- FX (monthly EURUSD, USD per EUR) ------------------------------------------------------
    fx: dict[date, float] = {month_end(2020, 12): 1.22}
    level = 1.22
    for day in months:
        level *= 1 + rng.gauss(0.0, 0.02)
        fx[day] = round(level, 5)
    write_parquet(out, "fx_rates", "dt=all",
                  [{"period_end": d.isoformat(), "pair": "EURUSD", "rate": r} for d, r in fx.items()])

    # --- estimates, finals, and what had been published by AS_OF -------------------------------
    rows_by_month: dict[date, list[dict[str, Any]]] = {}
    revisions: dict[str, list[float]] = {}
    quarterly: dict[date, list[dict[str, Any]]] = {}
    for mid, _n, strat, ccy, lag, freq, *_rest in MANAGERS:
        if freq == "Q":
            quarter_ends = [d for d in months if d.month % 3 == 0]
            for i, qd in enumerate(quarter_ends):
                chunk = [finals[mid][m] for m in months if qd - timedelta(days=89) <= m <= qd]
                value = math.prod(1 + v for v in chunk) - 1
                published = qd + timedelta(days=lag)
                if published <= AS_OF:
                    quarterly.setdefault(qd, []).append(
                        {"manager_id": mid, "period_end": qd.isoformat(), "return_local": value,
                         "published_date": published.isoformat()}
                    )
                del i
            continue
        noise = 0.0010 if lag == 15 else 0.0018
        for day, value in finals[mid].items():
            revision = (BIAS if mid == BIASED else 0.0) + noise * rng.gauss(0, 1)
            estimate = value - revision
            est_pub = day + timedelta(days=EST_DAYS)
            fin_pub = day + timedelta(days=lag)
            rows = rows_by_month.setdefault(day, [])
            if est_pub <= AS_OF:
                rows.append({"manager_id": mid, "period_end": day.isoformat(), "vintage": "estimate",
                             "return_local": estimate, "published_date": est_pub.isoformat()})
            if fin_pub <= AS_OF:
                rows.append({"manager_id": mid, "period_end": day.isoformat(), "vintage": "final",
                             "return_local": value, "published_date": fin_pub.isoformat()})
                if est_pub <= AS_OF:
                    revisions.setdefault(mid, []).append(revision)
    for day, rows in rows_by_month.items():
        write_parquet(out, "mgr_returns", f"dt={day.isoformat()}", rows)
    for day, rows in quarterly.items():
        write_parquet(out, "mgr_returns_q", f"dt={day.isoformat()}", rows)

    def stats(values: list[float]) -> dict[str, float]:
        mean = math.fsum(values) / len(values)
        sd = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))
        return {"n": len(values), "mean_revision": mean, "t_stat": mean / (sd / math.sqrt(len(values)))}

    answers["revisions"] = {
        "convention": "revision = final - estimate, simple monthly return in the manager's currency, "
        "over months where both vintages had been published at as_of; t = mean / (sd / sqrt(n))",
        "biased_manager": BIASED,
        "planted_mean_revision": BIAS,
        "managers": {m: stats(v) for m, v in revisions.items()},
    }

    # --- smoothing: the planted unsmoothed-to-reported volatility ratio ------------------------
    def sd(values: list[float]) -> float:
        mean = math.fsum(values) / len(values)
        return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))

    answers["smoothing"] = {
        "convention": "reported = theta0*R[t] + theta1*R[t-1] + theta2*R[t-2], R iid latent monthly "
        "returns; population ratio = 1/sqrt(sum theta^2); sample ratio = sd(R)/sd(reported), "
        "over the 60 months, sample sd (n-1), monthly, not annualised",
        "managers": {
            mid: {
                "theta": list(theta),
                "population_ratio": 1 / math.sqrt(sum(t * t for t in theta)),
                "sample_ratio": sd(LATENT[mid]) / sd([finals[mid][d] for d in months]),
            }
            for mid, theta in THETAS.items()
        },
    }

    # --- positions and the fund's own NAV (USD) ------------------------------------------------
    def to_usd(mid: str, ccy: str, day: date, value: float) -> float:
        if ccy == "USD":
            return value
        prior = month_end(day.year - (day.month == 1), (day.month - 2) % 12 + 1)
        return (1 + value) * fx[day] / fx[prior] - 1

    ccy_of = {m[0]: m[3] for m in MANAGERS}
    weights0 = {m[0]: rng.uniform(0.5, 1.5) for m in MANAGERS}
    total0 = math.fsum(weights0.values())
    value = {m: 800e6 * w / total0 for m, w in weights0.items()}
    quarterly_ret = {m[0]: m[5] == "Q" for m in MANAGERS}
    positions: dict[date, list[dict[str, Any]]] = {}
    fund_rows: list[dict[str, Any]] = []
    for day in months:
        start = math.fsum(value.values())
        for mid in value:
            if mid == LIQUIDATED and day > date(2024, 6, 30):
                value[mid] = 0.0
            elif quarterly_ret[mid]:
                if day.month % 3 == 0:
                    chunk = [to_usd(mid, ccy_of[mid], m, finals[mid][m]) for m in months
                             if day - timedelta(days=89) <= m <= day]
                    value[mid] *= math.prod(1 + v for v in chunk)
            else:
                value[mid] *= 1 + to_usd(mid, ccy_of[mid], day, finals[mid][day])
        end = math.fsum(value.values())
        positions[day] = [{"manager_id": m, "period_end": day.isoformat(), "value_usd": round(v, 2)}
                          for m, v in value.items()]
        fund_rows.append({"period_end": day.isoformat(), "nav_usd": round(end, 2),
                          "return": end / start - 1})
    for day, rows in positions.items():
        write_parquet(out, "fof_positions", f"dt={day.isoformat()}", rows)
    write_parquet(out, "fof_fund", "dt=all", fund_rows)
    answers["fund_nav_usd_end"] = fund_rows[-1]["nav_usd"]

    # --- public indices (daily, business days) -------------------------------------------------
    levels: dict[str, dict[date, float]] = {"SCI_US": {}, "SCI_EU": {}}
    cur = {"SCI_US": 100.0, "SCI_EU": 100.0}
    day = date(2015, 1, 1)
    while day <= date(2025, 12, 31):
        if day.weekday() < 5:
            for name, mu, sig in (("SCI_US", 0.0004, 0.010), ("SCI_EU", 0.0003, 0.011)):
                cur[name] *= 1 + rng.gauss(mu, sig)
                levels[name][day] = round(cur[name], 4)
        day += timedelta(days=1)
    for year in range(2015, 2026):
        write_parquet(out, "idx_public", f"year={year}", [
            {"date": d.isoformat(), "index_id": n, "level": v}
            for n, series in levels.items() for d, v in series.items() if d.year == year])

    # --- private markets: cash flows, NAVs, and the planted fund -------------------------------
    cashflows: dict[str, list[tuple[date, float]]] = {}
    navs: dict[str, dict[date, float]] = {}
    for fid, _n, cls, vintage, ccy, commit, _gp in PM_FUNDS:
        if fid == "PM01":  # the calibration fund: IRR exactly 20%, TVPI 1.32, fully realised
            cashflows[fid] = [(date(2021, 1, 1), -100e6), (date(2022, 1, 1), 60e6),
                              (date(2023, 1, 1), 72e6)]
            navs[fid] = {NAV_LAST: 0.0}
            continue
        growth, drate, first_dist = PM_PARAMS[cls]
        nav = 0.0
        flows: list[tuple[date, float]] = []
        nav_series: dict[date, float] = {}
        called = 0.0
        q = date(vintage, 3, 31)
        k = 0
        while q <= NAV_LAST:
            span = 90
            pace = max(0.0, 0.085 - 0.004 * k) * commit * rng.uniform(0.6, 1.4) if k < 20 else 0.0
            call = min(pace, 0.97 * commit - called)
            flow_day = q - timedelta(days=rng.randint(5, span - 5))
            dist = 0.0
            if k >= first_dist and nav > 0:
                dist = max(0.0, drate * nav * rng.uniform(0.4, 1.6))
            if call > 1:
                flows.append((flow_day, -round(call, 2)))
                called += call
            if dist > 1:
                flows.append((q - timedelta(days=rng.randint(3, 40)), round(dist, 2)))
            nav = max(0.0, nav * (1 + growth + rng.gauss(0, 0.012)) + call - dist)
            nav_series[q] = round(nav, 2)
            k += 1
            q = month_end(q.year + (q.month + 3 > 12), (q.month + 2) % 12 + 1)
        cashflows[fid] = sorted(flows)
        navs[fid] = nav_series
    for fid, flows in cashflows.items():
        write_parquet(out, "pm_cashflows", f"fund_id={fid}", [
            {"flow_date": d.isoformat(), "flow_type": "call" if a < 0 else "distribution",
             "amount_local": abs(a)} for d, a in flows])
    for qd in sorted({d for s in navs.values() for d in s}):
        write_parquet(out, "pm_nav", f"dt={qd.isoformat()}", [
            {"fund_id": f, "nav_date": qd.isoformat(), "nav_local": s[qd]}
            for f, s in navs.items() if qd in s])

    pm_truth: dict[str, Any] = {}
    for fid, _n, _cls, _v, ccy, *_r in PM_FUNDS:
        flows = cashflows[fid]
        nav_date = max(navs[fid])
        nav_value = navs[fid][nav_date]
        paid = -math.fsum(a for _, a in flows if a < 0)
        dist = math.fsum(a for _, a in flows if a > 0)
        idx = levels[INDEX_FOR[ccy]]
        end = index_on(idx, nav_date)
        ks = (
            math.fsum(a * end / index_on(idx, d) for d, a in flows if a > 0) + nav_value
        ) / math.fsum(-a * end / index_on(idx, d) for d, a in flows if a < 0)
        shares = math.fsum(-a / index_on(idx, d) for d, a in flows)  # index units still held
        ln_terminal = shares * end
        pm_truth[fid] = {
            "nav_date": nav_date.isoformat(), "paid_in": paid, "distributions": dist,
            "nav": nav_value, "tvpi": (dist + nav_value) / paid, "dpi": dist / paid,
            "rvpi": nav_value / paid, "irr": xirr([*flows, (nav_date, nav_value)]),
            "ks_pme": ks, "ln_pme_index_irr": xirr([*flows, (nav_date, ln_terminal)]),
        }
    answers["private_funds"] = {
        "convention": "paid_in = sum of calls; TVPI = (distributions + NAV) / paid_in; DPI = "
        "distributions / paid_in; RVPI = NAV / paid_in; IRR = XIRR (actual/365) on dated flows with "
        "NAV as a terminal inflow on the NAV date; KS-PME = (sum D*I_T/I_t + NAV) / sum C*I_T/I_t; "
        "LN-PME index IRR = XIRR of the same flows with the terminal value of the index units that "
        "mirror the calls and distributions; index level on the last business day on or before "
        "each date; all in the fund's currency",
        "calibration_fund": "PM01",
        "calibration": {"irr": 0.2, "tvpi": 1.32, "dpi": 1.32},
        "funds": pm_truth,
    }

    # --- raw: administrator NAV statements and GP capital-account statements -------------------
    raw = out / "raw"
    names = {"NGFS": "northgate-fund-services", "AHA": "alder-hill-administration"}
    for day in months[-3:]:
        for admin, folder in names.items():
            path = raw / "admin-nav" / folder / day.strftime("%Y%m")
            path.mkdir(parents=True, exist_ok=True)
            with (path / "nav-statement.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["manager_id", "period_end", "currency", "status", "mtd_return",
                                 "statement_date"])
                for m in MANAGERS:
                    if m[9] != admin or m[5] == "Q" or day not in finals[m[0]]:
                        continue
                    final = day + timedelta(days=m[4]) <= AS_OF
                    published = day + timedelta(days=m[4] if final else EST_DAYS)
                    found = next(r for r in rows_by_month[day] if r["manager_id"] == m[0]
                                 and r["vintage"] == ("final" if final else "estimate"))
                    writer.writerow([m[0], day.isoformat(), m[3], "final" if final else "estimate",
                                     repr(found["return_local"]), published.isoformat()])
    for fid, *_ in PM_FUNDS:
        path = raw / "gp-statements" / fid
        path.mkdir(parents=True, exist_ok=True)
        last_flows = [(d, a) for d, a in cashflows[fid] if NAV_LAST - timedelta(days=89) <= d <= NAV_LAST]
        with (path / "2025Q3-capital-account.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["fund_id", "quarter_end", "contributions", "distributions", "ending_nav"])
            writer.writerow([fid, NAV_LAST.isoformat(), -math.fsum(a for _, a in last_flows if a < 0),
                             math.fsum(a for _, a in last_flows if a > 0), navs[fid][NAV_LAST]])

    # --- Unity Catalog `ref` schema ------------------------------------------------------------
    managers: list[dict[str, Any]] = []
    terms: list[dict[str, Any]] = []
    for m in MANAGERS:
        mid, name, strat, ccy, lag, freq, *_x, admin = m
        liquidated = mid == LIQUIDATED
        managers.append({"manager_id": mid, "name": name, "strategy": strat, "currency": ccy,
                         "administrator": admin, "nav_lag_days": lag, "frequency": freq,
                         "inception_date": "2020-12-31",
                         "status": "liquidated" if liquidated else "active",
                         "liquidated_date": "2024-06-30" if liquidated else None})
        lock, notice, redeem, gate = TERMS[strat]
        invested = date(2020 + rng.randint(0, 3), rng.randint(1, 12), 1)
        terms.append({"manager_id": mid, "lockup_months": lock, "notice_days": notice,
                      "redemption_freq_months": redeem, "gate_pct": gate,
                      "investment_date": invested.isoformat(),
                      "side_pocket_pct": 0.05 if strat in ("illiquid_credit", "real_assets") else 0.0})
    write_parquet(out, "managers", "", managers)
    write_parquet(out, "terms", "", terms)
    write_parquet(out, "pm_funds", "", [
        {"fund_id": f[0], "name": f[1], "asset_class": f[2], "vintage_year": f[3],
         "currency": f[4], "commitment_local": f[5], "gp": f[6], "index_id": INDEX_FOR[f[4]]}
        for f in PM_FUNDS])
    breaks = {("EQ02", months[-2]): 2500.0, ("EV01", months[-3]): -4100.0,
              ("MS02", months[-1]): 18000.0}
    recon = [{"manager_id": r["manager_id"], "period_end": day.isoformat(),
              "our_value_usd": r["value_usd"],
              "admin_value_usd": r["value_usd"] - breaks.get((r["manager_id"], day), 0.0)}
             for day in months[-3:] for r in positions[day]]
    write_parquet(out, "nav_recon", "", recon)
    answers["nav_recon_breaks"] = {
        "convention": "our_value_usd - admin_value_usd, breaks above 1,000 USD in absolute value",
        "breaks": [{"manager_id": m, "period_end": d.isoformat(), "diff_usd": x}
                   for (m, d), x in sorted(breaks.items())],
    }
    answers["liquidated"] = {"manager_id": LIQUIDATED, "last_return_month": "2024-06-30"}
    answers["availability_at_as_of"] = {
        "convention": "a final is present only when period_end + nav_lag_days <= as_of",
        "last_final_month_by_lag": {
            "15": max(d for d in months if d + timedelta(days=15) <= AS_OF).isoformat(),
            "45": max(d for d in months if d + timedelta(days=45) <= AS_OF).isoformat(),
        },
        "last_quarterly_final": max(quarterly).isoformat(),
    }
    write_delta_logs()
    (out / "lake_map.json").write_text(json.dumps({
        f"abfss://raw@{HOST}": "raw", f"abfss://uc@{HOST}": "uc"}, indent=2))
    (out / "known_answers.json").write_text(json.dumps(answers, indent=2, sort_keys=True))
    return answers


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    build(args.out, args.seed)


if __name__ == "__main__":
    main()
