"""A firm's own metric set in `analyze` and the tear sheet, with `all_metrics` the default."""

from __future__ import annotations

import numpy as np
import pytest

from quant_toolbox.data import demo_returns
from quant_toolbox.performance import Series, all_metrics, analyze
from quant_toolbox.report import tear_sheet


def house_metrics(
    returns: Series, periods_per_year: int, risk_free: float = 0.0, *, backend: str | None = None
) -> dict[str, float]:
    """The standard set, plus the worst single bar."""
    standard = all_metrics(returns, periods_per_year, risk_free, backend=backend)
    return {**standard, "worst_bar": float(np.nanmin(np.asarray(returns, dtype=np.float64)))}


def only_hit_rate(
    returns: Series, periods_per_year: int, risk_free: float = 0.0, *, backend: str | None = None
) -> dict[str, float]:
    """A set that replaces the standard one."""
    values = np.asarray(returns, dtype=np.float64)
    return {"hit_rate": float(np.mean(values[np.isfinite(values)] > 0.0))}


def _fund() -> tuple[list, np.ndarray]:
    table = demo_returns(300)
    return table.dates, table.column("Macro")


def test_the_default_is_all_metrics_and_is_not_named() -> None:
    dates, returns = _fund()
    analysis = analyze(dates, returns, 252)
    assert analysis.metrics == all_metrics(returns, 252)
    assert "metrics" not in analysis.conventions


def test_a_metric_set_that_extends_the_standard_one() -> None:
    dates, returns = _fund()
    analysis = analyze(dates, returns, 252, metrics=house_metrics)
    assert analysis.metrics["worst_bar"] == pytest.approx(float(np.min(returns)))
    assert analysis.metrics["sharpe_ratio"] == all_metrics(returns, 252)["sharpe_ratio"]
    assert analysis.conventions["metrics"].endswith("house_metrics")


def test_a_metric_set_that_replaces_the_standard_one_shows_in_the_tear_sheet() -> None:
    dates, returns = _fund()
    assert list(analyze(dates, returns, 252, metrics=only_hit_rate).metrics) == ["hit_rate"]
    for metrics, expected in ((only_hit_rate, ["Hit rate"]), (house_metrics, ["Worst bar"])):
        document = tear_sheet(dates, returns, 252, metrics=metrics)
        rows = [row[0] for row in document.blocks[0].rows]  # type: ignore[union-attr]
        assert rows[-1] == expected[-1]


def test_a_metric_set_that_returns_something_else_is_refused() -> None:
    dates, returns = _fund()

    def broken(returns: Series, periods_per_year: int, risk_free: float = 0.0, *, backend=None):  # type: ignore[no-untyped-def]
        return {"name": "not a number"}

    with pytest.raises(TypeError, match="not a number"):
        analyze(dates, returns, 252, metrics=broken)
