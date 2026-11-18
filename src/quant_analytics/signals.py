"""Signal studies and backtests over a panel of dates by instruments, on Quant Toolbox's research.

`quant_analytics.tools` reads a signal frame and a returns frame by reference and aligns them
into a `Panel`; this module computes on it. The toolbox works on prices, so the returns become a
wealth index first (`prices_from_returns`), which gives exactly the same forward returns.

Conventions, stated in every output:
- **Returns.** Row t is the simple return over the bar that ends on date t, as a fraction.
- **Timing.** A signal on date t is known at that close and traded ``lag`` bars later
  (`quant_toolbox.research.TRADE_LAG`, one bar, by default), so it is scored against, and
  earns, the return from bar t + lag onwards. ``lag=0`` trades on the signal's own close, the
  most optimistic choice, and is warned about.
- **Missing values.** A missing signal leaves that instrument out on that date. A missing return
  is no move, and the instrument cannot be traded on that date. An instrument whose returns stop
  before the last date is treated as delisted and earns ``delisting_return`` once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from quant_agent_os.evidence import Evidence, SourceRef
from quant_toolbox import data, research
from quant_toolbox import performance as metrics

MIN_BARS = data.MIN_OBSERVATIONS
LOOK_AHEAD_IC = 0.3  # a mean rank IC this large at the trade is more often a leak than skill
TOP_DRAWDOWNS = 3
Weighting = Literal["dollar_neutral", "long_only"]
FloatArray = npt.NDArray[np.float64]

SIGNAL_CONVENTIONS = {
    "returns": "simple returns as fractions; row t is the return over the bar ending on date t",
    "timing": "a signal on date t is known at that close and traded lag bars later; it is "
    "scored against the return from bar t + lag to bar t + lag + horizon",
    "ic": "per-date Pearson correlation of signal and forward return across instruments "
    f"(at least {research.MIN_PAIRS} pairs), averaged over dates",
    "rank_ic": "per-date Spearman correlation, ties at their average rank, averaged over dates",
    "rank_ic_ir": "mean rank IC over its standard deviation across dates (ddof 1); per date, "
    "not annualised",
    "t_stat": "Newey-West t-statistic of the mean rank IC, with at least horizon - 1 lags for "
    "overlapping forward returns",
    "quantiles": "each date's instruments split by signal rank into equal buckets, lowest "
    "signal first, scored on the one-bar return after the trade; a date with fewer than two "
    "instruments per bucket is left out; mean_returns are means over dates, per bar",
    "spread": "top bucket minus bottom bucket, per bar, averaged over dates; its t-statistic "
    "is Newey-West",
    "signal_autocorrelation": "mean rank correlation of the signal with itself one date "
    "earlier; near 1 means low turnover",
    "missing": "a missing signal leaves the instrument out on that date; a missing return is "
    "no move and no trade; returns that stop before the last date are a delisting",
}
BACKTEST_CONVENTIONS = {
    "returns": SIGNAL_CONVENTIONS["returns"],
    "simulation": "a historical simulation of rules applied to past data; not a forecast, a "
    "track record, or investment advice",
    "timing": "weights built from the signal on date t are first held over bar t + 1 + lag",
    "weights": "dollar_neutral: each date's signal ranks, centred and scaled to gross exposure "
    "1 (net 0); long_only: equal weights on the top 1/quantiles of instruments, summing to 1",
    "signal_gaps": "a date with no signal for any instrument keeps the previous date's signal; "
    "a single missing signal leaves that instrument out",
    "rebalancing": "the book trades to its target every rebalance_every bars and drifts with "
    "returns in between",
    "costs": "cost_bps per unit of one-way turnover, charged on actual trades, net = gross - "
    "costs; turnover is one-way, half the sum of absolute trades",
    "period": "from the first bar holding a position to the last date",
    "annualized_return": "geometric, of net returns",
    "cost_drag": "annualised arithmetic mean of gross minus net returns; gross_return and "
    "net_return in costs are arithmetic too",
    "break_even_bps": "the one cost rate per unit of turnover at which the mean net return is 0",
}


@dataclass(frozen=True)
class Panel:
    """A signal and returns over the same dates and instruments (NaN where missing)."""

    dates: list[date]
    instruments: list[str]
    signal: FloatArray
    returns: FloatArray
    sources: list[SourceRef]


def prices_from_returns(returns: FloatArray) -> FloatArray:
    """A wealth index with one more row: row j + 1 is the close of bar j, row 0 the close before.

    An instrument is priced from the close before its first return; a missing return is a
    missing price, bridged by the last one, and returns that stop make prices that stop.
    """
    valid = np.isfinite(returns)
    wealth = np.cumprod(1.0 + np.where(valid, returns, 0.0), axis=0)
    prices = np.vstack([np.ones((1, returns.shape[1])), wealth])
    prices[1:][~valid] = np.nan
    listed = valid.any(axis=0)
    first = np.argmax(valid, axis=0)
    for column in np.flatnonzero(listed):
        prices[: first[column], column] = np.nan
        prices[first[column], column] = (
            1.0 if first[column] == 0 else wealth[first[column] - 1, column]
        )
    prices[:, ~listed] = np.nan
    return prices


def _padded(values: FloatArray) -> FloatArray:
    """``values`` with a missing first row, to line up with `prices_from_returns`."""
    return np.vstack([np.full((1, values.shape[1]), np.nan), values])


def _defined(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _stopped(panel: Panel) -> list[str]:
    """Instruments whose returns stop before the last date."""
    valid = np.isfinite(panel.returns)
    last = panel.returns.shape[0] - 1 - np.argmax(valid[::-1], axis=0)
    return [
        name
        for at, name in enumerate(panel.instruments)
        if valid[:, at].any() and last[at] < panel.returns.shape[0] - 1
    ]


def _common_warnings(panel: Panel, lag: int, delisting_return: float) -> list[str]:
    warnings: list[str] = []
    finite = panel.returns[np.isfinite(panel.returns)]
    warnings += data.percent_like("the returns", finite.tolist())
    if lag == 0:
        warnings.append(
            "lag is 0: trades happen on the signal's own close, which assumes the signal and the "
            "trade at the same price; results are optimistic."
        )
    stopped = _stopped(panel)
    if stopped:
        shown = ", ".join(stopped[:10]) + (" and others" if len(stopped) > 10 else "")
        warnings.append(
            f"Returns stop before the last date for {len(stopped)} instruments ({shown}); each "
            f"is treated as delisted and earns {delisting_return:.0%} once. Pass "
            "delisting_return 0 if they are not delistings."
        )
    return warnings


class HorizonIc(BaseModel):
    """Information coefficients at one forward horizon."""

    horizon: int
    mean_ic: float | None
    mean_rank_ic: float | None
    rank_ic_std: float | None
    rank_ic_ir: float | None
    rank_ic_t_stat: float | None
    positive_rank_ic_share: float | None
    dates: int = Field(description="Dates with a rank IC")


class QuantileReturns(BaseModel):
    """Mean one-bar return of each signal bucket and the top-minus-bottom spread."""

    quantiles: int
    mean_returns: list[float | None] = Field(description="Per bar, lowest signal bucket first")
    spread: float | None
    spread_t_stat: float | None
    dates: int = Field(description="Dates with every bucket filled")


class SignalSummary(BaseModel):
    """The numbers to quote, at the first horizon."""

    horizon: int
    mean_rank_ic: float | None
    mean_ic: float | None
    rank_ic_ir: float | None
    rank_ic_t_stat: float | None
    top_minus_bottom: float | None = Field(description="Mean spread per bar")
    spread_t_stat: float | None
    dates: int
    instruments: int


class SignalObservations(BaseModel):
    """How much data the study used."""

    dates: int = Field(description="Dates shared by the signal and the returns")
    instruments: int
    signal_dates: int = Field(description="Dates with a signal for at least one instrument")
    mean_names_per_date: float | None = Field(
        description="Instruments with a signal and a forward return, per scored date"
    )
    first_date: str
    last_date: str


class SignalOutput(BaseModel):
    """A signal's predictive power: summary first, then ICs, decay, and quantile returns."""

    summary: SignalSummary
    signal: str
    returns: str
    lag: int
    horizons: list[HorizonIc]
    quantile_returns: QuantileReturns
    signal_autocorrelation: float | None
    observations: SignalObservations
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


def analyze_signal(
    panel: Panel,
    labels: tuple[str, str],
    *,
    horizons: list[int],
    quantiles: int,
    lag: int,
    delisting_return: float,
) -> SignalOutput:
    """IC, rank IC, IR, and HAC t-statistic per horizon, quantile returns, and autocorrelation."""
    prices = prices_from_returns(panel.returns)
    signal = _padded(panel.signal)
    per_horizon = []
    for horizon in horizons:
        found = research.ic_summary(signal, prices, horizon, lag, delisting_return)
        per_horizon.append(
            HorizonIc(
                horizon=horizon,
                mean_ic=_defined(found["mean_ic"]),
                mean_rank_ic=_defined(found["mean_rank_ic"]),
                rank_ic_std=_defined(found["rank_ic_std"]),
                rank_ic_ir=_defined(found["rank_ic_ir"]),
                rank_ic_t_stat=_defined(found["rank_ic_t_stat"]),
                positive_rank_ic_share=_defined(found["positive_rank_ic_share"]),
                dates=int(found["bars"]),
            )
        )
    buckets = research.quantile_returns(signal, prices, quantiles, lag, delisting_return)
    spread = buckets[:, -1] - buckets[:, 0]
    filled = np.isfinite(spread)
    means = [
        _defined(float(np.nanmean(buckets[:, q]))) if np.isfinite(buckets[:, q]).any() else None
        for q in range(quantiles)
    ]
    mean_spread = float(np.mean(spread[filled])) if filled.any() else math.nan
    spread_t = research.hac_t_stat(spread, 1) if filled.sum() > 2 else math.nan
    quantile = QuantileReturns(
        quantiles=quantiles,
        mean_returns=means,
        spread=_defined(mean_spread),
        spread_t_stat=_defined(spread_t),
        dates=int(filled.sum()),
    )

    first = horizons[0]
    forward = research.forward_returns(prices, first, lag, delisting_return)[1:]
    pairs = (np.isfinite(panel.signal) & np.isfinite(forward)).sum(axis=1)
    scored = pairs[pairs > 0]
    has_signal = np.isfinite(panel.signal).any(axis=1)
    observations = SignalObservations(
        dates=len(panel.dates),
        instruments=len(panel.instruments),
        signal_dates=int(has_signal.sum()),
        mean_names_per_date=float(scored.mean()) if scored.size else None,
        first_date=panel.dates[0].isoformat(),
        last_date=panel.dates[-1].isoformat(),
    )
    head = per_horizon[0]
    summary = SignalSummary(
        horizon=first,
        mean_rank_ic=head.mean_rank_ic,
        mean_ic=head.mean_ic,
        rank_ic_ir=head.rank_ic_ir,
        rank_ic_t_stat=head.rank_ic_t_stat,
        top_minus_bottom=quantile.spread,
        spread_t_stat=quantile.spread_t_stat,
        dates=head.dates,
        instruments=len(panel.instruments),
    )

    warnings = _common_warnings(panel, lag, delisting_return)
    warnings += data.short_sample(f"rank ICs at {first} bar(s)", head.dates, MIN_BARS)
    few = int(((pairs > 0) & (pairs < research.MIN_PAIRS)).sum())
    if few:
        warnings.append(
            f"{few} dates have fewer than {research.MIN_PAIRS} instruments with a signal and a "
            "forward return, so they have no IC."
        )
    thin = int(((pairs >= research.MIN_PAIRS) & (pairs < 2 * quantiles)).sum())
    if thin:
        warnings.append(
            f"{thin} dates have fewer than {2 * quantiles} instruments, two per bucket, so they "
            "are left out of the quantile returns."
        )
    if head.mean_rank_ic is not None and abs(head.mean_rank_ic) >= LOOK_AHEAD_IC:
        warnings.append(
            f"A mean rank IC of {head.mean_rank_ic:.2f} is unusually high; check that the signal "
            "on each date uses only data known at that close."
        )

    signal_id, returns_id = labels
    statements = []
    if head.mean_rank_ic is not None:
        t_text = f", HAC t {head.rank_ic_t_stat:.2f}" if head.rank_ic_t_stat is not None else ""
        statements.append(
            f"Mean rank IC of {signal_id} against {returns_id} at {first} bar(s) is "
            f"{head.mean_rank_ic:.4f}{t_text}, over {head.dates} dates."
        )
    if head.rank_ic_ir is not None:
        statements.append(f"Rank IC IR of {signal_id} at {first} bar(s) is {head.rank_ic_ir:.3f}.")
    if quantile.spread is not None:
        statements.append(
            f"Top minus bottom of {quantiles} buckets of {signal_id} earns "
            f"{quantile.spread:.4%} a bar on average, over {quantile.dates} dates."
        )
    return SignalOutput(
        summary=summary,
        signal=signal_id,
        returns=returns_id,
        lag=lag,
        horizons=per_horizon,
        quantile_returns=quantile,
        signal_autocorrelation=_defined(research.signal_autocorrelation(panel.signal)),
        observations=observations,
        conventions=SIGNAL_CONVENTIONS | {"delisting_return": f"{delisting_return:g}"},
        warnings=warnings,
        evidence=[
            Evidence(kind="computed", statement=statement, sources=panel.sources)
            for statement in statements
        ],
    )


class BacktestSummary(BaseModel):
    """The numbers to quote, of net returns over the backtest's period."""

    annualized_return: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    hit_rate: float | None
    annual_turnover: float | None
    cost_drag: float | None
    first_date: str
    last_date: str
    bars: int


class BacktestCosts(BaseModel):
    """What trading costs took, annualised; returns here are arithmetic means."""

    cost_bps: float
    gross_return: float | None
    net_return: float | None
    cost_drag: float | None
    annual_turnover: float | None
    break_even_bps: float | None


class BacktestBook(BaseModel):
    """The positions the backtest held, on average over its bars."""

    weighting: Weighting
    lag: int
    rebalance_every: int
    mean_gross_exposure: float
    mean_net_exposure: float
    mean_positions: float
    forced_exits: int = Field(description="Positions written off at a delisting")


class DrawdownSummary(BaseModel):
    """One of the deepest drawdowns of net returns."""

    depth: float
    peak: str | None
    trough: str
    recovered: str | None


class BacktestOutput(BaseModel):
    """A simulated strategy's net returns: summary first, then the analysis behind it."""

    summary: BacktestSummary
    signal: str
    returns: str
    costs: BacktestCosts
    book: BacktestBook
    metrics: dict[str, float | None]
    benchmark: dict[str, float | None] | None
    distribution: dict[str, float | None]
    sharpe_inference: dict[str, float | None]
    drawdowns: list[DrawdownSummary]
    periods_per_year: int
    conventions: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)


def _carried(signal: FloatArray) -> tuple[FloatArray, int]:
    """The signal with each empty date taking the previous date's, and how many were empty."""
    result = signal.copy()
    empty = ~np.isfinite(signal).any(axis=1)
    carried = 0
    for row in range(1, signal.shape[0]):
        if empty[row] and np.isfinite(result[row - 1]).any():
            result[row] = result[row - 1]
            carried += 1
    return result, carried


def backtest(
    panel: Panel,
    labels: tuple[str, str],
    *,
    weighting: Weighting,
    quantiles: int,
    lag: int,
    cost_bps: float,
    rebalance_every: int,
    periods_per_year: int,
    benchmark: list[float] | None,
    risk_free: float,
    delisting_return: float,
) -> BacktestOutput:
    """Turn the signal into weights, backtest them with costs and lag, and analyse net returns.

    ``benchmark``, when given, has one return per date of the panel (NaN where missing).
    """
    signal, carried = _carried(panel.signal)
    if weighting == "long_only":
        weights = research.long_only_weights(signal, quantiles)
    else:
        weights = research.dollar_neutral_weights(signal)
    prices = prices_from_returns(panel.returns)
    padded = np.vstack([np.zeros((1, weights.shape[1])), weights])
    found = research.run_backtest(
        prices,
        padded,
        cost_bps=cost_bps,
        lag=lag,
        rebalance_every=rebalance_every,
        delisting_return=delisting_return,
    )
    held = found.held[1:]
    active = np.flatnonzero(np.abs(held).sum(axis=1) > 0.0)
    if active.size == 0:
        raise ValueError(
            "The signal never led to a position: no date has enough instruments with a signal "
            "and a price to trade, before the last date."
        )
    start = int(active[0])
    window = research.BacktestResult(
        gross=found.gross[1:][start:],
        net=found.net[1:][start:],
        turnover=found.turnover[1:][start:],
        held=held[start:],
        forced_exits=found.forced_exits[1:][start:],
        costs=found.costs[1:][start:],
    )
    dates = panel.dates[start:]
    drag = research.cost_drag(window, periods_per_year)
    reference = None if benchmark is None else np.asarray(benchmark[start:], dtype=np.float64)
    analysis = metrics.analyze(
        dates,
        window.net,
        periods_per_year,
        benchmark=reference,
        risk_free=risk_free,
        horizons=(),
    )
    values = {name: _defined(value) for name, value in analysis.metrics.items()}
    summary = BacktestSummary(
        annualized_return=values["annualized_return"],
        annualized_volatility=values["annualized_volatility"],
        sharpe_ratio=values["sharpe_ratio"],
        max_drawdown=values["max_drawdown"],
        hit_rate=values["hit_rate"],
        annual_turnover=_defined(drag.annual_turnover),
        cost_drag=_defined(drag.cost_drag),
        first_date=dates[0].isoformat(),
        last_date=dates[-1].isoformat(),
        bars=len(dates),
    )
    gross_exposure = np.abs(window.held).sum(axis=1)
    book = BacktestBook(
        weighting=weighting,
        lag=lag,
        rebalance_every=rebalance_every,
        mean_gross_exposure=float(gross_exposure.mean()),
        mean_net_exposure=round(float(window.held.sum(axis=1).mean()), 12),
        mean_positions=float((window.held != 0.0).sum(axis=1).mean()),
        forced_exits=int(window.forced_exits.sum()),
    )
    shape = analysis.distribution
    warnings = _common_warnings(panel, lag, delisting_return)
    warnings += [note.replace("the returns", "the net returns") for note in analysis.notes]
    if carried:
        warnings.append(
            f"{carried} dates have no signal for any instrument and keep the previous date's "
            "signal."
        )
    if benchmark is not None:
        gaps = int((~np.isfinite(reference)).sum()) if reference is not None else 0
        if gaps:
            warnings.append(
                f"The benchmark has no return on {gaps} of the backtest's dates; benchmark "
                "measures use the dates with both."
            )
    if values["sharpe_ratio"] is not None and values["sharpe_ratio"] > 3.0:
        warnings.append(
            f"A Sharpe ratio of {values['sharpe_ratio']:.2f} is unusually high for a simulation; "
            "check the signal for look-ahead (data not known at each date's close) and the costs."
        )
    signal_id, returns_id = labels
    name = f"the backtest of {signal_id}"
    statements = [
        f"Simulated annualised net return of {name} is {_percent(values['annualized_return'])}, "
        f"from {summary.first_date} to {summary.last_date}, after {cost_bps:g} bps costs.",
        f"Simulated Sharpe ratio of {name} is {_ratio(values['sharpe_ratio'])}.",
        f"Maximum drawdown of {name} is {_percent(values['max_drawdown'])}.",
        f"Annual one-way turnover of {name} is {_ratio(drag.annual_turnover)} times, and costs "
        f"take {_percent(drag.cost_drag)} a year.",
    ]
    return BacktestOutput(
        summary=summary,
        signal=signal_id,
        returns=returns_id,
        costs=BacktestCosts(
            cost_bps=cost_bps,
            gross_return=_defined(drag.gross_return),
            net_return=_defined(drag.net_return),
            cost_drag=_defined(drag.cost_drag),
            annual_turnover=_defined(drag.annual_turnover),
            break_even_bps=_defined(drag.break_even_bps),
        ),
        book=book,
        metrics=values,
        benchmark=None
        if analysis.benchmark is None
        else {key: _defined(value) for key, value in analysis.benchmark.items()},
        distribution={
            "skewness": _defined(shape.skewness),
            "excess_kurtosis": _defined(shape.excess_kurtosis),
            "best_period": _defined(shape.best),
            "worst_period": _defined(shape.worst),
        },
        sharpe_inference={k: _defined(v) for k, v in analysis.sharpe_inference.items()},
        drawdowns=[
            DrawdownSummary(
                depth=episode.depth,
                peak=episode.peak.isoformat() if episode.peak else None,
                trough=episode.trough.isoformat(),
                recovered=episode.recovered.isoformat() if episode.recovered else None,
            )
            for episode in analysis.drawdowns[:TOP_DRAWDOWNS]
        ],
        periods_per_year=periods_per_year,
        conventions=BACKTEST_CONVENTIONS
        | {
            key: analysis.conventions[key]
            for key in ("source", "annualized_volatility", "sharpe_ratio", "max_drawdown")
        }
        | {
            "hit_rate": analysis.conventions["hit_rate"],
            "delisting_return": f"{delisting_return:g}",
        },
        warnings=warnings,
        evidence=[
            Evidence(kind="computed", statement=statement, sources=panel.sources)
            for statement in statements
        ],
    )


def _percent(value: float | None) -> str:
    return "undefined" if value is None or not math.isfinite(value) else f"{value:.2%}"


def _ratio(value: float | None) -> str:
    return "undefined" if value is None or not math.isfinite(value) else f"{value:.2f}"
