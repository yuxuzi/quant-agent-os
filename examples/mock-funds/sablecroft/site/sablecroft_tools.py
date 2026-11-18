"""Sablecroft's own tools: plain typed Python that knows nothing about the platform.

`sablecroft_server.py` registers them (decision 0024). Each states its convention; the known
answers are in tests/mock_funds/test_sablecroft.py. pandas objects arrive with the dataset's dates
as a DatetimeIndex (series by date) or manager and fund ids as the index (one row per name).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

STRATEGIES = (
    "eq_ls",
    "macro",
    "event",
    "relval",
    "cta",
    "multistrat",
    "illiquid_credit",
    "real_assets",
)
BUCKETS = (30, 90, 180, 365, 730)  # days from the as-of date to the cash arriving
PAYOUT_DAYS = 30


# ---- private markets -------------------------------------------------------------------------


def xirr(days: np.ndarray[Any, Any], amounts: np.ndarray[Any, Any]) -> float:
    """Return the rate r with sum(amount / (1 + r)^(days/365)) = 0, by Newton's method kept inside a
    bracket (bisection when a step leaves it). Raises ValueError without both signs."""
    if not (amounts > 0).any() or not (amounts < 0).any():
        raise ValueError("An IRR needs at least one outflow (a call) and one inflow.")

    def npv(rate: float) -> float:
        return float(np.sum(amounts / (1.0 + rate) ** (days / 365.0)))

    low, high = -0.9999, 1000.0
    rate = 0.1
    for _ in range(300):
        value = npv(rate)
        if abs(value) < 1e-9:
            return rate
        if (npv(low) > 0) == (value > 0):
            low = rate
        else:
            high = rate
        slope = float(np.sum(-days / 365.0 * amounts / (1.0 + rate) ** (days / 365.0 + 1.0)))
        step = rate - value / slope if slope else math.inf
        rate = step if low < step < high else (low + high) / 2.0
    return rate


@dataclass
class FundReturns:
    """One private fund's return and multiples at its last NAV."""

    nav_date: str
    paid_in: float
    distributions: float
    nav: float
    tvpi: float
    dpi: float
    rvpi: float
    irr: float
    warnings: list[str] = field(default_factory=list)


def _flows(
    calls: pd.Series, distributions: pd.Series, nav: pd.Series
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Timestamp, float, list[str]]:
    """Return net signed flows by date, the gross calls and distributions, and the last NAV."""
    last = nav.dropna()
    calls, distributions = calls.dropna(), distributions.dropna()
    if last.empty or (calls.empty and distributions.empty):
        raise ValueError("The fund needs at least one cash flow and one NAV.")
    nav_date = last.index[-1]
    warnings: list[str] = []
    late = int((calls.index > nav_date).sum() + (distributions.index > nav_date).sum())
    if late:
        warnings.append(
            f"{late} cash flows are dated after the last NAV ({nav_date.date()}) and were left out."
        )
    calls, distributions = (
        calls[calls.index <= nav_date],
        distributions[distributions.index <= nav_date],
    )
    flows = distributions.sub(calls, fill_value=0.0)
    flows = flows[flows != 0.0].sort_index()
    return flows, calls, distributions, nav_date, float(last.iloc[-1]), warnings


def private_fund_returns(calls: pd.Series, distributions: pd.Series, nav: pd.Series) -> FundReturns:
    """IRR, TVPI, DPI, and RVPI of one private fund from its dated cash flows and its NAVs.

    Args:
        calls: The fund's capital calls by date, as positive amounts in its currency; days without
            a call are missing.
        distributions: Its distributions by date, as positive amounts; days without one are missing.
        nav: The fund's quarter-end NAVs; the last one is the valuation date.
    """
    flows, gross_calls, gross_dists, nav_date, nav_value, warnings = _flows(
        calls, distributions, nav
    )
    paid, dist = float(gross_calls.sum()), float(gross_dists.sum())
    days = np.array([(d - flows.index[0]).days for d in [*flows.index, nav_date]], dtype=float)
    irr = xirr(days, np.array([*flows.to_numpy(float), nav_value]))
    return FundReturns(
        nav_date=str(nav_date.date()),
        paid_in=paid,
        distributions=dist,
        nav=nav_value,
        tvpi=(dist + nav_value) / paid,
        dpi=dist / paid,
        rvpi=nav_value / paid,
        irr=irr,
        warnings=warnings,
    )


@dataclass
class FundPme:
    """A private fund against a public index."""

    nav_date: str
    fund_irr: float
    ks_pme: float
    ln_index_irr: float | None
    ln_spread: float | None
    warnings: list[str] = field(default_factory=list)


def private_fund_pme(
    calls: pd.Series, distributions: pd.Series, nav: pd.Series, index: pd.Series
) -> FundPme:
    """Kaplan-Schoar and Long-Nickels PME of one private fund against a public index.

    Args:
        calls: The fund's capital calls by date, as positive amounts.
        distributions: Its distributions by date, as positive amounts.
        nav: The fund's quarter-end NAVs; the last one is the valuation date.
        index: Daily levels of the public index in the fund's currency.
    """
    flows, gross_calls, gross_dists, nav_date, nav_value, warnings = _flows(
        calls, distributions, nav
    )
    levels = index.dropna().sort_index()
    if flows.index[0] < levels.index[0]:
        raise ValueError(f"The index starts {levels.index[0].date()}, after the first cash flow.")
    every = (
        flows.index.union(gross_calls.index)
        .union(gross_dists.index)
        .append(pd.DatetimeIndex([nav_date]))
    )
    at = levels.reindex(levels.index.union(every)).ffill()
    end = float(at.loc[nav_date])
    # Kaplan-Schoar uses the gross flows; netting a call and a distribution of one day would
    # change the ratio.
    grow_c = end / at.loc[gross_calls.index].to_numpy(float)
    grow_d = end / at.loc[gross_dists.index].to_numpy(float)
    ks = (float(np.sum(gross_dists.to_numpy(float) * grow_d)) + nav_value) / float(
        np.sum(gross_calls.to_numpy(float) * grow_c)
    )
    units = float(np.sum(-flows.to_numpy(float) / at.loc[flows.index].to_numpy(float)))
    amounts = flows.to_numpy(float)
    days = np.array([(d - flows.index[0]).days for d in [*flows.index, nav_date]], dtype=float)
    fund_irr = xirr(days, np.array([*amounts, nav_value]))
    if units * end <= 0:
        warnings.append(
            "Distributions exceed what the calls would have bought in the index, so the "
            "Long-Nickels index IRR is undefined; use the Kaplan-Schoar PME."
        )
        return FundPme(str(nav_date.date()), fund_irr, ks, None, None, warnings)
    ln_irr = xirr(days, np.array([*amounts, units * end]))
    return FundPme(str(nav_date.date()), fund_irr, ks, ln_irr, fund_irr - ln_irr, warnings)


# ---- smoothed returns ------------------------------------------------------------------------


def _acf(values: np.ndarray[Any, Any], lag: int) -> float:
    centred = values - values.mean()
    return float(np.sum(centred[lag:] * centred[:-lag]) / np.sum(centred * centred))


@dataclass
class Unsmoothed:
    """A smoothed series' fitted profile and its volatility before smoothing."""

    months: int
    theta: list[float]
    rho1: float
    rho2: float
    reported_vol: float
    unsmoothed_vol: float
    vol_ratio: float
    warnings: list[str] = field(default_factory=list)


def unsmooth_returns(returns: pd.Series, periods_per_year: int = 12) -> Unsmoothed:
    """Volatility of a smoothed return series before smoothing, by a simplified
    Getmansky-Lo-Makarov fit.

    Model: reported[t] = t0*R[t] + t1*R[t-1] + t2*R[t-2], R iid, weights summing to 1 and not
    increasing (t0 >= t1 >= t2 >= 0). The weights are the grid point (step 0.01) whose implied lag-1
    and lag-2 autocorrelations are closest, in squared error, to the sample's; a method of moments,
    not the paper's maximum likelihood. Unsmoothed volatility is reported volatility divided by
    sqrt(t0^2 + t1^2 + t2^2). Volatilities are annualised by sqrt(periods_per_year).

    Args:
        returns: Monthly returns as fractions; missing months are dropped.
        periods_per_year: 12 for monthly returns.
    """
    values = returns.dropna().to_numpy(float)
    if len(values) < 24:
        raise ValueError(f"Unsmoothing needs at least 24 returns; there are {len(values)}.")
    rho1, rho2 = _acf(values, 1), _acf(values, 2)
    best: tuple[float, tuple[float, float, float]] = (math.inf, (1.0, 0.0, 0.0))
    for a in range(0, 61):
        for b in range(0, a + 1):
            t1, t2 = a / 100.0, b / 100.0
            t0 = 1.0 - t1 - t2
            if t0 < t1:
                continue
            norm = t0 * t0 + t1 * t1 + t2 * t2
            error = ((t0 * t1 + t1 * t2) / norm - rho1) ** 2 + (t0 * t2 / norm - rho2) ** 2
            if error < best[0] - 1e-15:
                best = (error, (t0, t1, t2))
    t0, t1, t2 = best[1]
    sd = float(values.std(ddof=1))
    ratio = 1.0 / math.sqrt(t0 * t0 + t1 * t1 + t2 * t2)
    annual = math.sqrt(periods_per_year)
    warnings = []
    if len(values) < 48:
        warnings.append("Fewer than 48 returns: the fitted weights are noisy.")
    return Unsmoothed(
        months=len(values),
        theta=[t0, t1, t2],
        rho1=rho1,
        rho2=rho2,
        reported_vol=sd * annual,
        unsmoothed_vol=sd * ratio * annual,
        vol_ratio=ratio,
        warnings=warnings,
    )


@dataclass
class LoSharpe:
    """An autocorrelation-adjusted Sharpe ratio."""

    months: int
    raw_sharpe: float
    eta: float
    adjusted_sharpe: float
    warnings: list[str] = field(default_factory=list)


def lo_adjusted_sharpe(returns: pd.Series, q: int = 12, risk_free_annual: float = 0.0) -> LoSharpe:
    """Sharpe ratio annualised with Lo's (2002) autocorrelation factor.

    Per-period Sharpe = mean excess return / sample sd (n-1). Raw annual = per-period * sqrt(q).
    Adjusted annual = per-period * eta(q), with eta(q) = q / sqrt(q + 2 * sum_{k=1}^{q-1} (q-k)
    rho_k)
    and rho_k the sample autocorrelations (demeaned, divided by the full-sample variance).

    Args:
        returns: Returns as fractions, one per period; missing values are dropped.
        q: Periods per year; 12 for monthly.
        risk_free_annual: Annual risk-free rate, spread evenly over the periods.
    """
    values = returns.dropna().to_numpy(float)
    if len(values) <= q:
        raise ValueError(f"Need more than {q} returns; there are {len(values)}.")
    per_period = float((values.mean() - risk_free_annual / q) / values.std(ddof=1))
    denominator = q + 2 * sum((q - k) * _acf(values, k) for k in range(1, q))
    if denominator <= 0:
        raise ValueError("The autocorrelations make the adjustment undefined (denominator <= 0).")
    eta = q / math.sqrt(denominator)
    return LoSharpe(len(values), per_period * math.sqrt(q), eta, per_period * eta)


# ---- the manager book ------------------------------------------------------------------------


@dataclass
class LadderBucket:
    """USD that can reach the fund within a number of days."""

    label: str
    usd: float
    cumulative_share: float


@dataclass
class ManagerLiquidity:
    """When one manager's first redemption pays."""

    manager_id: str
    nav_usd: float
    first_dealing_date: str
    first_payment_days: int
    tranches: int


@dataclass
class LiquidityLadder:
    """What the fund can turn into cash, by horizon."""

    as_of: str
    total_usd: float
    buckets: list[LadderBucket]
    managers: list[ManagerLiquidity]
    warnings: list[str] = field(default_factory=list)


def _dealing_dates(start: date, months: int) -> Any:
    """Yield dealing dates on or after ``start``: month-ends, quarter-ends, or each 31 December."""
    year, month = start.year, start.month
    while True:
        last = (
            (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
            if month < 12
            else date(year, 12, 31)
        )
        if last >= start and (month % months == 0):
            yield last
        month += 1
        if month > 12:
            month, year = 1, year + 1


def liquidity_ladder(
    terms: pd.DataFrame, as_of: str, payout_days: int = PAYOUT_DAYS
) -> LiquidityLadder:
    """Cash the fund can raise from its hedge-fund managers, by horizon, from their redemption
    terms.

    A redemption is requested on ``as_of``, or when the lock-up ends if later. The first dealing
    date
    is the first one on or after the request plus the notice period; dealing dates are month-ends,
    quarter-ends (frequency 3), or 31 December (frequency 12). The gate caps each dealing date at
    ``gate_pct`` of today's position (an investor-level gate, a simplification of fund-level gates);
    the rest goes at the following dealing dates. Cash arrives ``payout_days`` after dealing.
    Inactive
    managers and empty positions are left out.

    Args:
        terms: One row per manager: nav_usd, lockup_months, notice_days, redemption_freq_months,
            gate_pct, investment_yyyymmdd, active.
        as_of: The date, such as 2025-12-31.
        payout_days: Days from a dealing date to cash.
    """
    start = date.fromisoformat(as_of)
    totals = [0.0] * (len(BUCKETS) + 1)
    managers: list[ManagerLiquidity] = []
    for manager_id, row in terms.iterrows():
        if row["active"] < 1 or row["nav_usd"] <= 0:
            continue
        invested = str(int(row["investment_yyyymmdd"]))
        locked = date(int(invested[:4]), int(invested[4:6]), 1)
        month_index = locked.year * 12 + locked.month - 1 + int(row["lockup_months"])
        lock_end = date(month_index // 12, month_index % 12 + 1, 1)
        request = max(start, lock_end)
        earliest = request + timedelta(days=int(row["notice_days"]))
        left, position, tranches, first = float(row["nav_usd"]), float(row["nav_usd"]), 0, None
        for dealing in _dealing_dates(earliest, int(row["redemption_freq_months"])):
            part = min(left, position * float(row["gate_pct"]))
            days = (dealing - start).days + payout_days
            totals[next((i for i, b in enumerate(BUCKETS) if days <= b), len(BUCKETS))] += part
            first = first or (dealing, days)
            left -= part
            tranches += 1
            if left <= 1e-6:
                break
        assert first is not None
        managers.append(
            ManagerLiquidity(str(manager_id), position, str(first[0]), first[1], tranches)
        )
    total = math.fsum(totals)
    labels = [f"within {b} days" for b in BUCKETS] + [f"after {BUCKETS[-1]} days"]
    running, buckets = 0.0, []
    for label, usd in zip(labels, totals, strict=True):
        running += usd
        buckets.append(LadderBucket(label, usd, running / total if total else 0.0))
    return LiquidityLadder(as_of, total, buckets, managers)


@dataclass
class PeerRow:
    """One manager's trailing record and rank among its strategy's peers."""

    manager_id: str
    strategy: str
    months: int
    annual_return: float
    annual_vol: float
    sharpe: float
    rank: int
    peers: int


@dataclass
class PeerRanking:
    """Managers ranked by trailing Sharpe within their strategy."""

    window_months: int
    rows: list[PeerRow]
    skipped: list[str]
    warnings: list[str] = field(default_factory=list)


def manager_peer_ranking(
    returns: pd.DataFrame, terms: pd.DataFrame, window_months: int = 36, min_months: int = 24
) -> PeerRanking:
    """Rank managers by trailing Sharpe ratio within their strategy.

    Each manager's record is its non-missing returns among the last ``window_months`` rows of
    ``returns``. Annual return = (prod(1+r))^(12/n) - 1 over its n months; annual vol = sd(n-1) *
    sqrt(12); Sharpe = annual return / annual vol with a zero risk-free rate. A manager with fewer
    than ``min_months`` is skipped. Rank 1 is the highest Sharpe; ties share the lower rank.

    Args:
        returns: Monthly returns by manager (columns are manager ids).
        terms: One row per manager with strategy_code (1 = first of STRATEGIES).
        window_months: Trailing rows of ``returns`` to use.
        min_months: Fewest months a manager needs.
    """
    window = returns.tail(window_months)
    records: list[PeerRow] = []
    skipped: list[str] = []
    warnings: list[str] = []
    for manager in window.columns:
        values = window[manager].dropna().to_numpy(float)
        if manager not in terms.index:
            warnings.append(f"{manager} has no row in terms; skipped.")
            skipped.append(str(manager))
            continue
        if len(values) < min_months:
            skipped.append(str(manager))
            continue
        n = len(values)
        annual = float(np.prod(1 + values) ** (12.0 / n) - 1)
        vol = float(values.std(ddof=1) * math.sqrt(12))
        strategy = STRATEGIES[int(terms.loc[manager, "strategy_code"]) - 1]
        records.append(PeerRow(str(manager), strategy, n, annual, vol, annual / vol, 0, 0))
    for row in records:
        peers = [r for r in records if r.strategy == row.strategy]
        row.peers = len(peers)
        row.rank = 1 + sum(r.sharpe > row.sharpe for r in peers)
    records.sort(key=lambda r: (r.strategy, r.rank, r.manager_id))
    return PeerRanking(window_months, records, skipped, warnings)


@dataclass
class RevisionRow:
    """One manager's estimate-to-final revisions."""

    manager_id: str
    months: int
    mean_revision: float
    t_stat: float
    biased: bool


@dataclass
class RevisionBias:
    """Whether managers' estimates run systematically below or above their finals."""

    rows: list[RevisionRow]
    warnings: list[str] = field(default_factory=list)


def revision_bias(
    estimates: pd.DataFrame, finals: pd.DataFrame, t_threshold: float = 3.0
) -> RevisionBias:
    """Mean revision (final minus estimate) per manager, over the months where both exist.

    t = mean / (sd(n-1) / sqrt(n)); a manager is flagged when |t| exceeds ``t_threshold`` and it has
    at least 12 months. Returns are simple monthly returns in the manager's own currency.

    Args:
        estimates: Monthly estimated returns by manager.
        finals: Monthly final returns by manager, on the same dates or a subset.
        t_threshold: The |t| above which a manager is flagged.
    """
    rows: list[RevisionRow] = []
    for manager in finals.columns:
        if manager not in estimates.columns:
            continue
        both = pd.concat([finals[manager], estimates[manager]], axis=1, keys=["f", "e"]).dropna()
        if len(both) < 12:
            continue
        diff = (both["f"] - both["e"]).to_numpy(float)
        mean = float(diff.mean())
        t = mean / float(diff.std(ddof=1) / math.sqrt(len(diff)))
        rows.append(RevisionRow(str(manager), len(diff), mean, t, abs(t) > t_threshold))
    return RevisionBias(sorted(rows, key=lambda r: -abs(r.t_stat)))
