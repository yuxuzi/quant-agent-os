"""How a metric is shown: as a percentage, a ratio, or a count, by its name."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

PERCENT = frozenset(
    {
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "value_at_risk",
        "expected_shortfall",
        "hit_rate",
        "alpha",
        "active_return",
        "tracking_error",
        "volatility",
        "gross_annualized_return",
        "average_turnover",
        "annual_turnover",
        "positive_rank_ic_share",
        "best",
        "worst",
        "average_gain",
        "average_loss",
        "positive_share",
        "parametric_var",
        "parametric_expected_shortfall",
        "probabilistic_sharpe_ratio",
        "deflated_sharpe_ratio",
    }
)
COUNT = frozenset({"bars", "forced_exits", "delistings", "folds", "out_of_sample_bars"})
METRIC_KINDS: dict[str, str] = {name: "percent" for name in PERCENT} | {
    name: "count" for name in COUNT
}


def metric_kind(name: str, kinds: Mapping[str, str] | None = None) -> str:
    """``percent``, ``count``, or ``ratio`` (anything not named), for a metric's name."""
    return {**METRIC_KINDS, **(kinds or {})}.get(name, "ratio")


def format_metric(
    name: str, value: Any, *, ratio_digits: int = 4, kinds: Mapping[str, str] | None = None
) -> str:
    """Show a metric: percentages with two decimals, counts whole, ratios with ``ratio_digits``.

    None and NaN are ``n/a``, and text is shown as it is.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    kind = metric_kind(name, kinds)
    if kind == "count":
        return f"{value:.0f}"
    if kind == "percent":
        return f"{value:.2%}"
    return f"{value:.{ratio_digits}f}"


def label(name: str) -> str:
    """A metric's name as words: ``sharpe_ratio`` is ``Sharpe ratio``."""
    return name.replace("_", " ").capitalize()
