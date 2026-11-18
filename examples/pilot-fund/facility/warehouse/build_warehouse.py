"""Build Kestrel Point Capital's warehouse: a deterministic SQLite file of fictional data.

    python build_warehouse.py [PATH] [--seed N]

The same seed gives the same table contents. Beside the database it writes `known_answers.json`,
the truths planted in the data, so tools that read the warehouse can be checked against them.
All names and numbers are fictional.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]

DEFAULT_SEED = 20261117
DEFAULT_PATH = Path(__file__).with_name("kestrel.db")
INSTRUMENTS_FILE = Path(__file__).with_name("instruments.csv")
KNOWN_ANSWERS = "known_answers.json"
START, END = "2023-01-02", "2026-01-01"  # business days from START, before END
DAYS_PER_YEAR = 252
REBALANCE_EVERY = 5  # business days
COMMISSION_BPS = 1.0

FACTORS = ("equity_market", "rates", "usd", "commodity", "credit_spread")
FACTOR_NAMES = {
    "equity_market": "Global equity market",
    "rates": "Government bond duration (positive when bond prices rise)",
    "usd": "US dollar (positive when the dollar rises)",
    "commodity": "Broad commodities",
    "credit_spread": "Credit spread (positive when spreads tighten)",
}
FACTOR_VOL = np.array([0.16, 0.06, 0.08, 0.22, 0.05])  # annual
FACTOR_CORR = np.array(
    [
        [1.00, -0.25, -0.20, 0.30, 0.55],
        [-0.25, 1.00, 0.10, -0.15, -0.10],
        [-0.20, 0.10, 1.00, -0.30, -0.15],
        [0.30, -0.15, -0.30, 1.00, 0.20],
        [0.55, -0.10, -0.15, 0.20, 1.00],
    ]
)

DESKS = ("eq_ls", "macro", "commod")
DESK_CAPITAL = {"eq_ls": 400e6, "macro": 300e6, "commod": 150e6}

# Planted strengths: a signal at t adds kappa * signal * daily vol to the return at t + 1.
MOMENTUM_KAPPA = 0.03
VALUE_KAPPA = 0.018
SIGNAL_PERSISTENCE = 0.97


@dataclass(frozen=True)
class Instrument:
    """One row of `ref_instruments`, with the parameters its prices are drawn from."""

    instrument_id: str
    ticker: str
    name: str
    asset_class: str
    sector: str
    region: str
    currency: str
    desk: str
    base_price: float
    specific_vol: float  # annual
    loadings: dict[str, float] = field(default_factory=dict)
    carry_mean: float = 0.0  # annual


def load_instruments(path: Path = INSTRUMENTS_FILE) -> tuple[Instrument, ...]:
    """Read the instrument master and the parameters each one's prices are drawn from."""
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(
            Instrument(
                row["instrument_id"],
                row["ticker"],
                row["name"],
                row["asset_class"],
                row["sector"],
                row["region"],
                row["currency"],
                row["desk"],
                base_price=float(row["base_price"]),
                specific_vol=float(row["specific_vol"]),
                loadings={
                    name: float(value)
                    for name, _, value in (pair.partition("=") for pair in row["loadings"].split())
                },
                carry_mean=float(row["carry_mean"]),
            )
            for row in csv.DictReader(handle)
        )


INSTRUMENTS = load_instruments()


SCHEMA = """
CREATE TABLE ref_instruments (
    instrument_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, name TEXT NOT NULL,
    asset_class TEXT NOT NULL, sector TEXT NOT NULL, region TEXT NOT NULL,
    currency TEXT NOT NULL, desk TEXT NOT NULL
);
CREATE TABLE mkt_px_daily (
    date TEXT NOT NULL, instrument_id TEXT NOT NULL, close REAL NOT NULL,
    total_return REAL, PRIMARY KEY (date, instrument_id)
) WITHOUT ROWID;
CREATE TABLE pos_eod (
    date TEXT NOT NULL, desk TEXT NOT NULL, instrument_id TEXT NOT NULL,
    quantity REAL NOT NULL, market_value REAL NOT NULL, weight REAL NOT NULL,
    PRIMARY KEY (date, desk, instrument_id)
) WITHOUT ROWID;
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, desk TEXT NOT NULL,
    instrument_id TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
    price REAL NOT NULL, notional_usd REAL NOT NULL, commission_usd REAL NOT NULL
);
CREATE TABLE sig_daily (
    date TEXT NOT NULL, instrument_id TEXT NOT NULL, signal_name TEXT NOT NULL,
    value REAL NOT NULL, PRIMARY KEY (date, instrument_id, signal_name)
) WITHOUT ROWID;
CREATE TABLE bench_daily (
    date TEXT NOT NULL, benchmark_id TEXT NOT NULL, total_return REAL NOT NULL,
    PRIMARY KEY (date, benchmark_id)
) WITHOUT ROWID;
CREATE TABLE mkt_adv (
    date TEXT NOT NULL, instrument_id TEXT NOT NULL, adv_usd REAL NOT NULL,
    PRIMARY KEY (date, instrument_id)
) WITHOUT ROWID;
CREATE TABLE fm_factors (factor_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE fm_loadings (
    as_of_date TEXT NOT NULL, instrument_id TEXT NOT NULL, factor_id TEXT NOT NULL,
    loading REAL NOT NULL, PRIMARY KEY (as_of_date, instrument_id, factor_id)
) WITHOUT ROWID;
CREATE TABLE fm_factor_cov (
    as_of_date TEXT NOT NULL, factor_id_1 TEXT NOT NULL, factor_id_2 TEXT NOT NULL,
    cov_annual REAL NOT NULL, PRIMARY KEY (as_of_date, factor_id_1, factor_id_2)
) WITHOUT ROWID;
CREATE TABLE fm_specific (
    as_of_date TEXT NOT NULL, instrument_id TEXT NOT NULL, specific_var_annual REAL NOT NULL,
    PRIMARY KEY (as_of_date, instrument_id)
) WITHOUT ROWID;
"""

TABLES = (
    "ref_instruments",
    "mkt_px_daily",
    "pos_eod",
    "trades",
    "sig_daily",
    "bench_daily",
    "mkt_adv",
    "fm_factors",
    "fm_loadings",
    "fm_factor_cov",
    "fm_specific",
)


def business_days() -> list[str]:
    """Return the warehouse's business days as ISO dates."""
    days = np.arange(np.datetime64(START), np.datetime64(END))
    return [str(day) for day in days[np.is_busday(days)]]


def month_ends(dates: Sequence[str]) -> list[int]:
    """Return the indices of the last business day of each month."""
    return [i for i, day in enumerate(dates) if i + 1 == len(dates) or dates[i + 1][:7] != day[:7]]


# The data must not depend on the platform's BLAS, SIMD, or summation order, so the generator
# uses no linear algebra and sums with `math.fsum`, which is exactly rounded. Elementwise
# arithmetic is IEEE and the same everywhere; rounding the stored values hides the rest.


def _fsum_rows(values: Array) -> Array:
    return np.array([math.fsum(row) for row in values.tolist()])


def _cross_z(values: Array) -> Array:
    """Standardize each row across columns (population standard deviation)."""
    n = values.shape[1]
    mean = (_fsum_rows(values) / n)[:, None]
    std = np.sqrt(_fsum_rows((values - mean) ** 2) / n)[:, None]
    result: Array = (values - mean) / np.where(std > 0, std, 1.0)
    return result


def _ranks(values: Array) -> Array:
    """Rank each row, ties sharing their average rank, so column order cannot matter."""
    out = np.empty(values.shape)
    for r, row in enumerate(values):
        order = np.argsort(row, kind="stable")
        ranks = np.empty(len(row))
        ranks[order] = np.arange(len(row), dtype=np.float64)
        _, inverse, counts = np.unique(row, return_inverse=True, return_counts=True)
        if (counts > 1).any():
            sums = np.zeros(len(counts))
            np.add.at(sums, inverse, ranks)
            ranks = (sums / counts)[inverse]
        out[r] = ranks
    return out


def _cholesky(matrix: Array) -> Array:
    """The lower Cholesky factor, in plain Python, so no LAPACK is involved."""
    n = len(matrix)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            partial = math.fsum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                low[i][j] = math.sqrt(float(matrix[i, i]) - partial)
            else:
                low[i][j] = (float(matrix[i, j]) - partial) / low[j][j]
    return np.array(low)


def _times(a: Array, b: Array) -> Array:
    """``a @ b.T`` for a short inner dimension, summed in a fixed order without BLAS."""
    out = np.zeros((a.shape[0], b.shape[0]))
    for k in range(a.shape[1]):
        out = out + a[:, k : k + 1] * b[:, k]
    return out


def rank_ic(signal: Array, forward: Array) -> Array:
    """Return the cross-sectional Spearman correlation of each row pair."""
    a, b = _cross_z(_ranks(signal)), _cross_z(_ranks(forward))
    result: Array = _fsum_rows(a * b) / a.shape[1]
    return result


def _ar1(rng: np.random.Generator, days: int, columns: int, phi: float) -> Array:
    """Return a unit-variance AR(1) process per column."""
    shocks = rng.standard_normal((days, columns)) * np.sqrt(1.0 - phi * phi)
    out = np.empty((days, columns))
    out[0] = rng.standard_normal(columns)
    for t in range(1, days):
        out[t] = phi * out[t - 1] + shocks[t]
    return out


@dataclass
class _Market:
    dates: list[str]
    returns: Array  # days x instruments; row 0 is the first day's return, unused
    close: Array
    factor_returns: Array  # days x factors, daily
    loadings: Array  # instruments x factors
    momentum: Array
    carry: Array
    value: Array  # days x equities
    total_vol: Array  # instruments, daily


def _simulate_market(rng: np.random.Generator) -> _Market:
    dates = business_days()
    n_days, n_inst = len(dates), len(INSTRUMENTS)
    daily_vol = FACTOR_VOL / np.sqrt(DAYS_PER_YEAR)
    cov = FACTOR_CORR * np.outer(daily_vol, daily_vol)
    factor_returns = _times(rng.standard_normal((n_days, len(FACTORS))), _cholesky(cov))
    loadings = np.array([[inst.loadings.get(f, 0.0) for f in FACTORS] for inst in INSTRUMENTS])
    specific = np.array([inst.specific_vol for inst in INSTRUMENTS]) / np.sqrt(DAYS_PER_YEAR)
    factor_var = [
        math.fsum(
            row[f] * cov[f, g] * row[g] for f in range(len(FACTORS)) for g in range(len(FACTORS))
        )
        for row in loadings
    ]
    total_vol = np.sqrt(np.array(factor_var) + specific**2)

    momentum = _cross_z(_ar1(rng, n_days, n_inst, SIGNAL_PERSISTENCE))
    equities = [i for i, inst in enumerate(INSTRUMENTS) if inst.asset_class == "equity"]
    value_latent = _cross_z(_ar1(rng, n_days, len(equities), 0.995))
    carry_mean = np.array([inst.carry_mean for inst in INSTRUMENTS])
    carry_scale = np.maximum(np.abs(carry_mean), 0.01)
    carry = carry_mean + carry_scale * _ar1(rng, n_days, n_inst, 0.98)

    returns = _times(factor_returns, loadings) + rng.standard_normal((n_days, n_inst)) * specific
    returns[1:] += MOMENTUM_KAPPA * momentum[:-1] * total_vol
    returns[1:, equities] += VALUE_KAPPA * value_latent[:-1] * total_vol[equities]
    returns[0] = 0.0
    returns = np.round(returns, 8)
    base = np.array([inst.base_price for inst in INSTRUMENTS])
    close = np.round(base * np.cumprod(1.0 + returns, axis=0), 6)
    # Book-to-price: higher is cheaper; the planted part is the latent value score.
    value = np.round(0.45 + 0.12 * value_latent, 6)
    return _Market(
        dates,
        returns,
        close,
        factor_returns,
        loadings,
        np.round(momentum, 6),
        np.round(carry, 6),
        value,
        total_vol,
    )


@dataclass
class _Book:
    quantity: Array  # days x instruments, end of day
    nav: Array  # days
    trades: list[tuple[int, int, float]]  # day, instrument, signed quantity


def _desk_targets(market: _Market, desk: str, members: list[int], day: int) -> Array:
    mom = market.momentum[day, members]
    vol_annual = market.total_vol[members] * np.sqrt(DAYS_PER_YEAR)
    if desk == "eq_ls":
        val = _cross_z(market.value[day : day + 1])[0]
        score = 0.7 * mom + 0.3 * val
        score = score - math.fsum(score) / len(score)
        weights: Array = score / math.fsum(np.abs(score))
        return weights
    if desk == "macro":
        carry = market.carry[day, members]
        carry_z = _cross_z(carry[None, :])[0]
        score = 0.6 * mom + 0.4 * carry_z
        weights = np.clip(score * 0.03 / vol_annual, -1.5, 1.5)
        return weights
    weights = np.clip(mom * 0.04 / vol_annual, -0.4, 0.4)
    return weights


def _run_book(market: _Market, desk: str, members: list[int]) -> _Book:
    n_days = len(market.dates)
    quantity = np.zeros((n_days, len(members)))
    nav = np.zeros(n_days)
    nav[0] = DESK_CAPITAL[desk]
    trades: list[tuple[int, int, float]] = []
    held = np.zeros(len(members))
    for day in range(n_days):
        price = market.close[day, members]
        if day > 0:
            nav[day] = nav[day - 1] + math.fsum(held * (price - market.close[day - 1, members]))
        if day % REBALANCE_EVERY == 0:
            target = np.round(_desk_targets(market, desk, members, day) * nav[day] / price)
            for k, change in enumerate(target - held):
                if change != 0:
                    trades.append((day, members[k], float(change)))
            held = target
        quantity[day] = held
    return _Book(quantity, nav, trades)


def _rows_to_db(db: sqlite3.Connection, table: str, rows: Iterable[Sequence[Any]]) -> None:
    rows = list(rows)
    marks = ", ".join("?" * len(rows[0]))
    db.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)


def build(path: Path, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Write the warehouse at ``path`` and `known_answers.json` beside it; return the answers."""
    rng = np.random.default_rng(seed)
    market = _simulate_market(rng)
    dates = market.dates
    ids = [inst.instrument_id for inst in INSTRUMENTS]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    answers: dict[str, Any] = {
        "seed": seed,
        "determinism": (
            "the same seed gives the same tables; the generator avoids linear algebra and "
            "platform-dependent sums, so values should agree across platforms, and tests check "
            "only bounds on the planted truths and that these answers match the tables"
        ),
        "start": dates[0],
        "end": dates[-1],
    }

    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA)
        _rows_to_db(
            db,
            "ref_instruments",
            (
                (
                    i.instrument_id,
                    i.ticker,
                    i.name,
                    i.asset_class,
                    i.sector,
                    i.region,
                    i.currency,
                    i.desk,
                )
                for i in INSTRUMENTS
            ),
        )
        _rows_to_db(
            db,
            "mkt_px_daily",
            (
                (
                    day,
                    ids[i],
                    float(market.close[t, i]),
                    None if t == 0 else float(market.returns[t, i]),
                )
                for t, day in enumerate(dates)
                for i in range(len(ids))
            ),
        )
        equities = [i for i, inst in enumerate(INSTRUMENTS) if inst.asset_class == "equity"]
        carriers = [i for i, inst in enumerate(INSTRUMENTS) if inst.asset_class != "equity"]
        signal_rows: list[tuple[str, str, str, float]] = []
        for t, day in enumerate(dates):
            for i in range(len(ids)):
                signal_rows.append((day, ids[i], "momentum", float(market.momentum[t, i])))
            for i in carriers:
                signal_rows.append((day, ids[i], "carry", float(market.carry[t, i])))
            for k, i in enumerate(equities):
                signal_rows.append((day, ids[i], "value", float(market.value[t, k])))
        _rows_to_db(db, "sig_daily", signal_rows)

        desks: dict[str, Any] = {}
        position_rows: list[tuple[str, str, str, float, float, float]] = []
        trade_rows: list[tuple[str, str, str, str, str, float, float, float, float]] = []
        for desk in DESKS:
            members = [i for i, inst in enumerate(INSTRUMENTS) if inst.desk == desk]
            book = _run_book(market, desk, members)
            for t, day in enumerate(dates):
                for k, i in enumerate(members):
                    mv = round(float(book.quantity[t, k] * market.close[t, i]), 2)
                    position_rows.append(
                        (
                            day,
                            desk,
                            ids[i],
                            float(book.quantity[t, k]),
                            mv,
                            round(mv / float(book.nav[t]), 8),
                        )
                    )
            commissions = 0.0
            for day_index, i, change in book.trades:
                price = float(market.close[day_index, i])
                notional = round(abs(change) * price, 2)
                fee = round(notional * COMMISSION_BPS / 1e4, 2)
                commissions += fee
                trade_rows.append(
                    (
                        f"T{len(trade_rows) + 1:07d}",
                        dates[day_index],
                        desk,
                        ids[i],
                        "BUY" if change > 0 else "SELL",
                        abs(change),
                        price,
                        notional,
                        fee,
                    )
                )
            desks[desk] = {
                "instruments": len(members),
                "capital_usd": DESK_CAPITAL[desk],
                "gross_pnl_usd": round(float(book.nav[-1] - book.nav[0]), 2),
                "commissions_usd": round(commissions, 2),
                "final_nav_usd": round(float(book.nav[-1]), 2),
            }
        _rows_to_db(db, "pos_eod", position_rows)
        _rows_to_db(db, "trades", trade_rows)

        rates = {"2023": 0.050, "2024": 0.051, "2025": 0.043}
        eq_world = np.round(
            market.factor_returns[:, 0] + rng.standard_normal(len(dates)) * 0.0005, 8
        )
        bonds = market.factor_returns[:, 1]
        bench_rows: list[tuple[str, str, float]] = []
        for t, day in enumerate(dates[1:], start=1):
            cash = round(rates[day[:4]] / DAYS_PER_YEAR, 8)
            bench_rows.append((day, "cash_usd", cash))
            bench_rows.append((day, "eq_world", float(eq_world[t])))
            bench_rows.append(
                (day, "multi_asset_60_40", round(0.6 * float(eq_world[t]) + 0.4 * bonds[t], 8))
            )
        _rows_to_db(db, "bench_daily", bench_rows)

        adv_base = {
            "equity": 150e6,
            "govt_bond_future": 8e9,
            "fx": 30e9,
            "credit_index": 2e9,
            "commodity_future": 1.5e9,
        }
        ends = month_ends(dates)
        adv_rows = [
            (
                dates[t],
                ids[i],
                round(
                    adv_base[inst.asset_class]
                    * float(np.exp(rng.normal(0.0, 0.5 if t == ends[0] else 0.15))),
                    0,
                ),
            )
            for t in ends
            for i, inst in enumerate(INSTRUMENTS)
        ]
        _rows_to_db(db, "mkt_adv", adv_rows)

        _rows_to_db(db, "fm_factors", ((f, FACTOR_NAMES[f]) for f in FACTORS))
        loading_rows: list[tuple[str, str, str, float]] = []
        cov_rows: list[tuple[str, str, str, float]] = []
        specific_rows: list[tuple[str, str, float]] = []
        true_cov = FACTOR_CORR * np.outer(FACTOR_VOL, FACTOR_VOL)
        for t in ends:
            as_of = dates[t]
            noisy = market.loadings + rng.normal(0.0, 0.03, market.loadings.shape)
            noisy = np.where(market.loadings == 0.0, 0.0, noisy)
            for i in range(len(ids)):
                for f, factor in enumerate(FACTORS):
                    if noisy[i, f] != 0.0:
                        loading_rows.append((as_of, ids[i], factor, round(float(noisy[i, f]), 6)))
                vol = INSTRUMENTS[i].specific_vol * (1.0 + rng.normal(0.0, 0.05))
                specific_rows.append((as_of, ids[i], round(vol * vol, 8)))
            window = market.factor_returns[max(1, t - 119) : t + 1]
            cov = _covariance(window) * DAYS_PER_YEAR if len(window) >= 40 else true_cov
            for a, fa in enumerate(FACTORS):
                for b, fb in enumerate(FACTORS):
                    cov_rows.append((as_of, fa, fb, round(float(cov[a, b]), 8)))
        _rows_to_db(db, "fm_loadings", loading_rows)
        _rows_to_db(db, "fm_factor_cov", cov_rows)
        _rows_to_db(db, "fm_specific", specific_rows)

        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES
        }

    with closing(sqlite3.connect(path)) as db:
        db.execute("VACUUM")  # rows arrive out of key order; this packs the pages

    mom_ic = rank_ic(market.momentum[:-1], market.returns[1:])
    carry_ic = rank_ic(market.carry[:-1, carriers], market.returns[1:, carriers])
    value_ic = rank_ic(market.value[:-1], market.returns[1:, equities])
    answers.update(
        {
            "business_days": len(dates),
            "instruments": len(ids),
            "factors": list(FACTORS),
            "desks": desks,
            "row_counts": counts,
            "signals": {
                "convention": (
                    "rank IC: Spearman correlation across instruments between a signal on day t "
                    "and total_return on day t+1, averaged over days; IR is mean / std of the "
                    "daily IC; t-stat is mean / (std / sqrt(days))"
                ),
                "momentum": _ic_summary(mom_ic, "all 40 instruments", "planted, positive"),
                "carry": _ic_summary(carry_ic, "20 non-equity instruments", "planted, none"),
                "value": _ic_summary(value_ic, "20 equities", "planted, small positive"),
            },
            "benchmarks": ["cash_usd", "eq_world", "multi_asset_60_40"],
            "factor_vol_annual": dict(zip(FACTORS, FACTOR_VOL.tolist(), strict=True)),
        }
    )
    path.with_name(KNOWN_ANSWERS).write_text(json.dumps(answers, indent=2) + "\n", "utf-8")
    return answers


def _covariance(window: Array) -> Array:
    """The sample covariance of the columns, with exactly rounded sums."""
    n, k = window.shape
    centered = window - (_fsum_rows(window.T) / n)
    return np.array(
        [[math.fsum(centered[:, a] * centered[:, b]) / (n - 1) for b in range(k)] for a in range(k)]
    )


def _ic_summary(ic: Array, universe: str, truth: str) -> dict[str, Any]:
    mean = math.fsum(ic) / len(ic)
    std = math.sqrt(math.fsum((ic - mean) ** 2) / (len(ic) - 1))
    return {
        "universe": universe,
        "truth": truth,
        "mean_ic": round(mean, 10),
        "ic_ir": round(mean / std, 6),
        "t_stat": round(mean / (std / np.sqrt(len(ic))), 4),
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Build the warehouse from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    answers = build(args.path, args.seed)
    signals = answers["signals"]
    print(
        f"Wrote {args.path} ({answers['business_days']} days, {answers['instruments']} "
        f"instruments); momentum IC {signals['momentum']['mean_ic']:.4f}, "
        f"carry IC {signals['carry']['mean_ic']:.4f}, value IC {signals['value']['mean_ic']:.4f}."
    )


if __name__ == "__main__":
    main()
