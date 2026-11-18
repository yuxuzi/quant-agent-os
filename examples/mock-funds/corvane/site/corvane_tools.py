"""Corvane Equity Partners' benchmark-relative tools: plain Python, no platform imports.

`corvane_server.py` registers these as they are. Each takes weights, returns, and reference data
as pandas objects (the platform passes them by dataset reference) and states its convention.

Conventions shared by every tool:
- Weights are end-of-day weights as fractions of the account's value, cash included as the name
  ``CASH_USD``, so a long-only portfolio's weights sum to 1. A security not held has weight 0.
- A date is an ISO date that is a row of the weights dataset; a day with no row is refused.
- Returns are USD simple returns unless a parameter says local. A missing return is zero for
  ``CASH_USD`` and is warned about for any other name.
"""

from __future__ import annotations

import itertools
import math

import pandas as pd
from pydantic import BaseModel, Field

CASH = "CASH_USD"
DAYS_PER_YEAR = 252
SECTORS = (
    "Cash",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)  # ref_security.sector_code 1..12
CURRENCIES = ("EUR", "GBP", "JPY", "USD")  # ref_security.currency_code 1..4


class ToolInputError(ValueError):
    """Raised when a request cannot be answered, with what to change."""


def _row(frame: pd.DataFrame, as_of: str, name: str) -> pd.Series:
    stamp = pd.Timestamp(as_of)
    if stamp not in frame.index:
        nearest = ", ".join(
            d.date().isoformat()
            for d in frame.index[
                frame.index.searchsorted(stamp) - 1 : frame.index.searchsorted(stamp) + 1
            ]
        )
        raise ToolInputError(
            f"{as_of} is not a row of {name} (it runs {frame.index[0].date()} to "
            f"{frame.index[-1].date()}); the nearest dates are {nearest}."
        )
    row = frame.loc[stamp].fillna(0.0)
    assert isinstance(row, pd.Series)
    return row


def _compound(returns: pd.DataFrame, start: str, end: str) -> tuple[pd.Series, list[str]]:
    """Compound each column over the rows after ``start`` up to and including ``end``."""
    window = returns.loc[
        (returns.index > pd.Timestamp(start)) & (returns.index <= pd.Timestamp(end))
    ]
    if window.empty:
        raise ToolInputError(f"No returns after {start} up to {end}; check the dates.")
    missing = [c for c in window.columns if c != CASH and window[c].isna().all()]
    growth = (1.0 + window.fillna(0.0)).prod(axis=0) - 1.0
    assert isinstance(growth, pd.Series)
    return growth, missing


class ActiveShare(BaseModel):
    """Active share of one portfolio on one day."""

    as_of: str
    active_share: float = Field(description="Between 0 and 1")
    overlap: float = Field(description="Sum of the smaller weight in each name; 1 - active share")
    portfolio_names: int
    benchmark_names: int
    names_outside_benchmark: list[str]


def active_share(portfolio: pd.DataFrame, benchmark: pd.DataFrame, as_of: str) -> ActiveShare:
    """Active share: half the sum of absolute active weights over every name in either.

    Cash counts as a name with benchmark weight 0, so the cash buffer is part of active share.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        benchmark: The benchmark's drifted weights, dates by securities.
        as_of: The end-of-day date, such as 2026-05-15.
    """
    wp, wb = _row(portfolio, as_of, "portfolio"), _row(benchmark, as_of, "benchmark")
    names = sorted(set(wp.index) | set(wb.index))
    diff = pd.Series({n: wp.get(n, 0.0) - wb.get(n, 0.0) for n in names})
    share = 0.5 * float(diff.abs().sum())
    held = [n for n in wp.index if wp[n] > 0 and n != CASH]
    return ActiveShare(
        as_of=as_of,
        active_share=share,
        overlap=1.0 - share,
        portfolio_names=len(held),
        benchmark_names=int((wb > 0).sum()),
        names_outside_benchmark=sorted(n for n in held if wb.get(n, 0.0) <= 0),
    )


class TrackingError(BaseModel):
    """Ex-post tracking error over a window."""

    start: str
    end: str
    observations: int
    tracking_error: float = Field(description="Annualised")
    active_return: float = Field(description="Annualised mean daily active return")
    information_ratio: float | None


def tracking_error(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    start: str,
    end: str,
) -> TrackingError:
    """Ex-post tracking error: the sample standard deviation (n - 1) of daily active returns.

    Active return is the portfolio's return minus the benchmark's, day by day, over the days both
    have a return from ``start`` to ``end`` inclusive; annualised by the square root of 252. The
    information ratio is the mean daily active return times 252, over the tracking error.

    Args:
        portfolio_returns: The portfolio's daily gross returns.
        benchmark_returns: The benchmark's daily returns.
        start: First day included.
        end: Last day included.
    """
    both = pd.concat([portfolio_returns, benchmark_returns], axis=1, join="inner").dropna()
    both = both.loc[(both.index >= pd.Timestamp(start)) & (both.index <= pd.Timestamp(end))]
    if len(both) < 3:
        raise ToolInputError(f"Fewer than 3 days of both returns between {start} and {end}.")
    active = both.iloc[:, 0] - both.iloc[:, 1]
    te = float(active.std(ddof=1)) * math.sqrt(DAYS_PER_YEAR)
    mean = float(active.mean()) * DAYS_PER_YEAR
    return TrackingError(
        start=start,
        end=end,
        observations=len(both),
        tracking_error=te,
        active_return=mean,
        information_ratio=mean / te if te > 0 else None,
    )


class ExAnte(BaseModel):
    """Ex-ante tracking error from a trailing window."""

    as_of: str
    window_days: int
    tracking_error: float = Field(description="Annualised")
    active_weight_gross: float = Field(description="Sum of absolute active weights, cash excluded")


def tracking_error_ex_ante(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns: pd.DataFrame,
    as_of: str,
    window_days: int = 60,
) -> ExAnte:
    """Ex-ante tracking error by historical simulation of today's active weights.

    The standard deviation (n - 1) of the series active weights . daily USD returns over the last
    ``window_days`` days ending ``as_of``, annualised by the square root of 252. This is the
    sample covariance of the securities without any factor model, shrinkage, or decay. Cash is
    left out, and a missing return is zero.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        benchmark: The benchmark's drifted weights, dates by securities.
        returns: Daily USD returns, dates by securities.
        as_of: The date whose weights are held fixed.
        window_days: Trailing days of returns, 20 to 250.
    """
    if not 20 <= window_days <= 250:
        raise ToolInputError("window_days must be between 20 and 250.")
    wp, wb = _row(portfolio, as_of, "portfolio"), _row(benchmark, as_of, "benchmark")
    names = [n for n in sorted(set(wp.index) | set(wb.index)) if n != CASH]
    active = pd.Series({n: wp.get(n, 0.0) - wb.get(n, 0.0) for n in names})
    upto = returns.loc[returns.index <= pd.Timestamp(as_of)].tail(window_days)
    if len(upto) < window_days:
        raise ToolInputError(f"Only {len(upto)} days of returns up to {as_of}; need {window_days}.")
    series = upto.reindex(columns=names).fillna(0.0).mul(active, axis=1).sum(axis=1)
    return ExAnte(
        as_of=as_of,
        window_days=window_days,
        tracking_error=float(series.std(ddof=1)) * math.sqrt(DAYS_PER_YEAR),
        active_weight_gross=float(active.abs().sum()),
    )


class SectorEffect(BaseModel):
    """One sector's Brinson-Fachler effects."""

    sector: str
    portfolio_weight: float | None = None
    benchmark_weight: float | None = None
    allocation: float
    selection: float
    interaction: float
    total: float


class Attribution(BaseModel):
    """Brinson-Fachler attribution of one span."""

    start: str
    end: str
    method: str
    portfolio_return: float
    benchmark_return: float
    active_return: float
    allocation: float
    selection: float
    interaction: float
    sectors: list[SectorEffect]
    warnings: list[str] = Field(default_factory=list)


def _sector_names(codes: pd.Series, names: list[str]) -> pd.Series:
    unknown = sorted(set(names) - set(codes.index))
    mapped = codes.reindex(names)
    if mapped.isna().any() or unknown:
        raise ToolInputError(f"No sector code for {', '.join(unknown[:5])}.")
    return mapped.astype(int).map(lambda c: SECTORS[c - 1])


def _period(
    wp: pd.Series,
    wb: pd.Series,
    growth: pd.Series,
    sector: pd.Series,
) -> tuple[float, float, dict[str, dict[str, float]]]:
    """Brinson-Fachler for one period from start weights and each name's period return."""
    names = sector.index
    p = wp.reindex(names).fillna(0.0)
    b = wb.reindex(names).fillna(0.0)
    r = growth.reindex(names).fillna(0.0)
    rp, rb = float((p * r).sum()), float((b * r).sum())
    effects: dict[str, dict[str, float]] = {}
    for name in sorted(set(sector)):
        members = sector.index[sector == name]
        pw, bw = float(p[members].sum()), float(b[members].sum())
        br = float((b[members] * r[members]).sum()) / bw if bw else rb
        pr = float((p[members] * r[members]).sum()) / pw if pw else br
        effects[name] = {
            "pw": pw,
            "bw": bw,
            "allocation": (pw - bw) * (br - rb),
            "selection": bw * (pr - br),
            "interaction": (pw - bw) * (pr - br),
        }
    return rp, rb, effects


def _assemble(
    start: str,
    end: str,
    method: str,
    rp: float,
    rb: float,
    effects: dict[str, dict[str, float]],
    warnings: list[str],
) -> Attribution:
    rows = [
        SectorEffect(
            sector=name,
            portfolio_weight=e.get("pw"),
            benchmark_weight=e.get("bw"),
            allocation=e["allocation"],
            selection=e["selection"],
            interaction=e["interaction"],
            total=e["allocation"] + e["selection"] + e["interaction"],
        )
        for name, e in effects.items()
    ]
    return Attribution(
        start=start,
        end=end,
        method=method,
        portfolio_return=rp,
        benchmark_return=rb,
        active_return=rp - rb,
        allocation=sum(r.allocation for r in rows),
        selection=sum(r.selection for r in rows),
        interaction=sum(r.interaction for r in rows),
        sectors=rows,
        warnings=warnings,
    )


def _prepare(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns: pd.DataFrame,
    sector_code: pd.Series,
    start: str,
    end: str,
) -> tuple[pd.Series, pd.Series, pd.Series, list[str]]:
    wp, wb = _row(portfolio, start, "portfolio"), _row(benchmark, start, "benchmark")
    names = sorted(set(wp[wp > 0].index) | set(wb[wb > 0].index))
    growth, missing = _compound(returns.reindex(columns=names), start, end)
    warnings = [f"No return for {n} between {start} and {end}; treated as 0." for n in missing]
    return wp, wb, growth, warnings


def attribution_single(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns: pd.DataFrame,
    sector_code: pd.Series,
    start: str,
    end: str,
) -> Attribution:
    """Brinson-Fachler by sector over one buy-and-hold period.

    Weights are the end-of-day weights on ``start``; each name's return is its USD returns
    compounded over the days after ``start`` up to ``end``. Per sector, allocation is
    (wp - wb)(Rb_sector - Rb), selection is wb(Rp_sector - Rb_sector), and interaction is
    (wp - wb)(Rp_sector - Rb_sector); they sum to Rp - Rb. A sector with no benchmark weight
    takes the total benchmark return as its benchmark return, and one with no portfolio weight
    takes its benchmark return, so neither produces an effect from nothing. Cash is its own
    sector. Names bought during the period (a spin-off) are not in the start weights, so the
    modelled portfolio return can differ from the books'.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        benchmark: The benchmark's drifted weights, dates by securities.
        returns: Daily USD returns, dates by securities.
        sector_code: Each security's sector code (ref_security.sector_code).
        start: The date whose end-of-day weights open the period.
        end: The date that closes it.
    """
    wp, wb, growth, warnings = _prepare(portfolio, benchmark, returns, sector_code, start, end)
    names = [n for n in sorted(set(wp[wp > 0].index) | set(wb[wb > 0].index))]
    sector = _sector_names(sector_code, names)
    sector.index = pd.Index(names)
    rp, rb, effects = _period(wp, wb, growth, sector)
    return _assemble(start, end, "single period, buy and hold", rp, rb, effects, warnings)


def _carino(rp: float, rb: float) -> float:
    if abs(rp - rb) < 1e-12:
        return 1.0 / (1.0 + rp)
    return (math.log1p(rp) - math.log1p(rb)) / (rp - rb)


class Period(BaseModel):
    """One period of a linked attribution."""

    start: str
    end: str
    portfolio_return: float
    benchmark_return: float
    scale: float = Field(description="Carino scale applied to the period's effects")


class LinkedAttribution(Attribution):
    """A multi-period attribution and its periods."""

    periods: list[Period]


def attribution_linked(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns: pd.DataFrame,
    sector_code: pd.Series,
    start: str,
    end: str,
    frequency: str = "monthly",
) -> LinkedAttribution:
    """Multi-period Brinson-Fachler, linked by the Carino method.

    The span is cut into periods, each attributed as in attribution_single from its first day's
    weights: ``monthly`` cuts at the first trading day of each month (the days the fund
    rebalances), ``daily`` at every day. Each period's effects are scaled by k_t / K, where
    k = (ln(1+Rp) - ln(1+Rb)) / (Rp - Rb) for the period and K the same for the whole span, so
    the linked effects add up exactly to the span's compounded portfolio return minus its
    compounded benchmark return. Periods' returns come from start weights, so they equal the
    books' returns only when nothing is bought or sold inside a period.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        benchmark: The benchmark's drifted weights, dates by securities.
        returns: Daily USD returns, dates by securities.
        sector_code: Each security's sector code (ref_security.sector_code).
        start: The first period's opening date.
        end: The last period's closing date.
        frequency: ``monthly`` or ``daily``.
    """
    if frequency not in ("monthly", "daily"):
        raise ToolInputError("frequency must be monthly or daily.")
    days = portfolio.index[
        (portfolio.index >= pd.Timestamp(start)) & (portfolio.index <= pd.Timestamp(end))
    ]
    if len(days) < 2:
        raise ToolInputError(f"Fewer than two dates of the portfolio between {start} and {end}.")
    edges = [days[0]]
    for before, day in itertools.pairwise(days):
        if frequency == "daily" or day.month != before.month:
            edges.append(day)
    if edges[-1] != days[-1]:
        edges.append(days[-1])
    warnings: list[str] = []
    results: list[tuple[str, str, float, float, dict[str, dict[str, float]]]] = []
    for a, b in itertools.pairwise(edges):
        s, e = a.date().isoformat(), b.date().isoformat()
        wp, wb, growth, w = _prepare(portfolio, benchmark, returns, sector_code, s, e)
        warnings += w
        names = sorted(set(wp[wp > 0].index) | set(wb[wb > 0].index))
        sector = _sector_names(sector_code, names)
        sector.index = pd.Index(names)
        rp, rb, effects = _period(wp, wb, growth, sector)
        results.append((s, e, rp, rb, effects))
    cum_p = math.prod(1.0 + r[2] for r in results) - 1.0
    cum_b = math.prod(1.0 + r[3] for r in results) - 1.0
    big_k = _carino(cum_p, cum_b)
    linked: dict[str, dict[str, float]] = {}
    periods: list[Period] = []
    for s, e, rp, rb, effects in results:
        scale = _carino(rp, rb) / big_k
        periods.append(
            Period(start=s, end=e, portfolio_return=rp, benchmark_return=rb, scale=scale)
        )
        for name, values in effects.items():
            row = linked.setdefault(name, {"allocation": 0.0, "selection": 0.0, "interaction": 0.0})
            for kind in row:
                row[kind] += values[kind] * scale
    base = _assemble(
        edges[0].date().isoformat(),
        edges[-1].date().isoformat(),
        f"{len(results)} {frequency} periods, Carino-linked",
        cum_p,
        cum_b,
        linked,
        warnings,
    )
    return LinkedAttribution(**base.model_dump(), periods=periods)


class CurrencyEffect(BaseModel):
    """The currency part of a global portfolio's active return, in USD."""

    start: str
    end: str
    active_return_usd: float
    active_return_local: float
    currency_allocation: float
    residual: float
    benchmark_currency_return: float
    by_currency: dict[str, float] = Field(description="Allocation effect by currency")


def currency_effect(
    portfolio: pd.DataFrame,
    benchmark: pd.DataFrame,
    returns_usd: pd.DataFrame,
    returns_local: pd.DataFrame,
    fx_returns: pd.DataFrame,
    currency_code: pd.Series,
    start: str,
    end: str,
) -> CurrencyEffect:
    """Split a USD active return into local-market and currency effects, buy and hold.

    From the start day's weights: the active return in USD is sum (wp - wb) R_usd; the local
    active return is the same with local returns; the currency allocation effect is
    sum over currencies of (wp_c - wb_c)(e_c - e_B), with e_c the currency's return against the
    dollar over the period and e_B the benchmark's currency-weighted e_c; the residual is what
    is left (the cross of local and currency returns). A missing local return is zero, so the
    market-closed days of a holiday are currency-only.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        benchmark: The benchmark's drifted weights, dates by securities.
        returns_usd: Daily USD returns, dates by securities.
        returns_local: Daily local-currency returns, dates by securities.
        fx_returns: Daily currency returns against USD, dates by currency (EUR GBP JPY USD).
        currency_code: Each security's currency code (ref_security.currency_code): 1 EUR, 2 GBP,
            3 JPY, 4 USD.
        start: The date whose end-of-day weights open the period.
        end: The date that closes it.
    """
    wp, wb = _row(portfolio, start, "portfolio"), _row(benchmark, start, "benchmark")
    names = sorted(set(wp[wp > 0].index) | set(wb[wb > 0].index))
    ccy = currency_code.reindex(names)
    if ccy.isna().any():
        raise ToolInputError("Some held securities have no currency code.")
    ccy = ccy.astype(int).map(lambda c: CURRENCIES[c - 1])
    usd, _ = _compound(returns_usd.reindex(columns=names), start, end)
    local, _ = _compound(returns_local.reindex(columns=names), start, end)
    fx, _ = _compound(fx_returns, start, end)
    active = wp.reindex(names).fillna(0.0) - wb.reindex(names).fillna(0.0)
    bench_ccy = {c: float(wb.reindex(names).fillna(0.0)[ccy == c].sum()) for c in CURRENCIES}
    e_b = sum(bench_ccy[c] * float(fx[c]) for c in CURRENCIES)
    by_currency = {c: float(active[ccy == c].sum()) * (float(fx[c]) - e_b) for c in CURRENCIES}
    total = float((active * usd).sum())
    local_active = float((active * local).sum())
    allocation = sum(by_currency.values())
    return CurrencyEffect(
        start=start,
        end=end,
        active_return_usd=total,
        active_return_local=local_active,
        currency_allocation=allocation,
        residual=total - local_active - allocation,
        benchmark_currency_return=e_b,
        by_currency=by_currency,
    )


class Liquidity(BaseModel):
    """A portfolio's days-to-liquidate profile."""

    as_of: str
    participation: float
    aum_usd: float
    max_days: float
    median_days: float
    stock_weight: float
    liquidated_by_day: dict[str, float] = Field(
        description="Share of the equity holdings' value sold within 1, 5, 10, and 20 days"
    )
    slowest: list[str]


def days_to_liquidate(
    portfolio: pd.DataFrame,
    adv_usd: pd.DataFrame,
    as_of: str,
    aum_usd: float,
    participation: float = 0.2,
) -> Liquidity:
    """Days to sell each holding at a share of its average daily volume, and the profile.

    A holding's days are its USD value (weight times ``aum_usd``) over participation times its
    20-row average daily traded value in USD (the adv_usd dataset). The profile gives the share of
    the equity holdings' value that is sold within k days, each name selling at most
    participation times ADV per day. Cash is left out. A holding with no ADV that day is listed
    in the warnings of an error, never skipped.

    Args:
        portfolio: The portfolio's weights, dates by securities.
        adv_usd: Average daily traded value in USD, dates by securities.
        as_of: The date.
        aum_usd: The portfolio's value in USD, which the weights do not carry.
        participation: Share of a day's volume the fund may trade, 0.01 to 1.
    """
    if not 0.01 <= participation <= 1.0:
        raise ToolInputError("participation must be between 0.01 and 1.")
    weights = _row(portfolio, as_of, "portfolio")
    held = weights[(weights > 0) & (weights.index != CASH)]
    _row(adv_usd, as_of, "adv_usd")  # refuses a day with no row
    raw = adv_usd.loc[pd.Timestamp(as_of)].reindex(held.index)
    if raw.isna().any() or (raw <= 0).any():
        bad = ", ".join(str(n) for n in held.index[raw.isna() | (raw <= 0)][:8])
        raise ToolInputError(f"No average daily volume on {as_of} for {bad}.")
    days = held * aum_usd / (participation * raw)
    profile = {
        str(k): float(sum(min(w, k * w / d) for w, d in zip(held, days, strict=True)) / held.sum())
        for k in (1, 5, 10, 20)
    }
    return Liquidity(
        as_of=as_of,
        participation=participation,
        aum_usd=aum_usd,
        max_days=float(days.max()),
        median_days=float(days.median()),
        stock_weight=float(held.sum()),
        liquidated_by_day=profile,
        slowest=[str(n) for n in days.sort_values(ascending=False).index[:5]],
    )


class Breach(BaseModel):
    """One day a mandate rule was broken."""

    date: str
    security_id: str
    kind: str
    weight: float


class MandateCheck(BaseModel):
    """The days a portfolio broke its mandate between two dates."""

    start: str
    end: str
    days_checked: int
    breaches: list[Breach]
    breach_days: list[str]


def mandate_check(
    portfolio: pd.DataFrame,
    excluded: pd.Series,
    start: str,
    end: str,
    max_position: float | None = None,
) -> MandateCheck:
    """Check a mandate's restrictions on every day from ``start`` to ``end``.

    A breach is a positive weight in a security flagged excluded (the account's own
    mandate_flags dataset, built from its client restrictions), or, with ``max_position``, a
    weight above it in any security other than cash. Weights are end-of-day, so a name received
    and sold the same day does not show.

    Args:
        portfolio: The account's weights, dates by securities.
        excluded: 1 for each security the mandate forbids, 0 otherwise.
        start: First day checked.
        end: Last day checked.
        max_position: Largest weight a single security may hold, such as 0.05; none to skip.
    """
    window = portfolio.loc[
        (portfolio.index >= pd.Timestamp(start)) & (portfolio.index <= pd.Timestamp(end))
    ]
    if window.empty:
        raise ToolInputError(f"No portfolio dates between {start} and {end}.")
    flags = excluded.reindex(window.columns).fillna(0.0)
    found: list[Breach] = []
    for stamp, row in window.fillna(0.0).iterrows():
        for name, weight in row.items():
            if weight <= 0:
                continue
            if flags[name] > 0:
                found.append(
                    Breach(
                        date=stamp.date().isoformat(),
                        security_id=str(name),
                        kind="excluded",
                        weight=float(weight),
                    )
                )
            if max_position is not None and name != CASH and weight > max_position:
                found.append(
                    Breach(
                        date=stamp.date().isoformat(),
                        security_id=str(name),
                        kind="max_position",
                        weight=float(weight),
                    )
                )
    return MandateCheck(
        start=start,
        end=end,
        days_checked=len(window),
        breaches=found,
        breach_days=sorted({b.date for b in found}),
    )
