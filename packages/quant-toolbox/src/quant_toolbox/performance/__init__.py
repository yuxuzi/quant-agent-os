"""Performance: return metrics with stated conventions, horizons, drawdowns, and attribution.

The metrics empyrical-reloaded has are computed by it when it is installed, and by the numpy
backend otherwise; both answer the same, and `backend` says which ran. Each private module's
docstring states its conventions, and `conventions` returns them as text.

`analyze` gathers the rest into one typed result: metrics, a benchmark comparison with capture
ratios, the distribution, Sharpe inference, horizons, and drawdowns. Also here: rolling metrics
as values, parametric value at risk, the standard error, probabilistic and deflated Sharpe
ratios and minimum track record, and single-period Brinson attribution.
"""

from quant_toolbox._types import BoolArray, FloatArray
from quant_toolbox.performance._analysis import HORIZONS, Analysis, analyze
from quant_toolbox.performance._attribution import Attribution, GroupAttribution, brinson
from quant_toolbox.performance._backend import (
    BACKEND_VARIABLE,
    BACKENDS,
    backend,
    backend_version,
    source,
)
from quant_toolbox.performance._conventions import conventions
from quant_toolbox.performance._distribution import (
    VAR_METHODS,
    Distribution,
    excess_kurtosis,
    parametric_expected_shortfall,
    parametric_var,
    return_distribution,
    skewness,
)
from quant_toolbox.performance._horizons import (
    Drawdown,
    calendar_years,
    drawdowns,
    horizon_return,
    horizon_start,
    is_horizon,
    monthly_returns,
    months_before,
    underwater,
    wealth,
)
from quant_toolbox.performance._inference import (
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    minimum_track_record,
    probabilistic_sharpe_ratio,
    sharpe_standard_error,
)
from quant_toolbox.performance._metrics import (
    METRIC_NAMES,
    Series,
    active_metrics,
    all_metrics,
    alpha,
    annualized_return,
    annualized_volatility,
    bar_returns,
    benchmark_metrics,
    beta,
    calmar_ratio,
    capture_ratios,
    expected_shortfall,
    hit_rate,
    max_drawdown,
    omega_ratio,
    sharpe_ratio,
    simple_returns,
    sortino_ratio,
    stability,
    summarize,
    tail_ratio,
    turnover,
    value_at_risk,
)
from quant_toolbox.performance._rolling import (
    rolling_beta,
    rolling_correlation,
    rolling_sharpe,
    rolling_volatility,
)

__all__ = [
    "BACKENDS",
    "BACKEND_VARIABLE",
    "HORIZONS",
    "METRIC_NAMES",
    "VAR_METHODS",
    "Analysis",
    "Attribution",
    "BoolArray",
    "Distribution",
    "Drawdown",
    "FloatArray",
    "GroupAttribution",
    "Series",
    "active_metrics",
    "all_metrics",
    "alpha",
    "analyze",
    "annualized_return",
    "annualized_volatility",
    "backend",
    "backend_version",
    "bar_returns",
    "benchmark_metrics",
    "beta",
    "brinson",
    "calendar_years",
    "calmar_ratio",
    "capture_ratios",
    "conventions",
    "deflated_sharpe_ratio",
    "drawdowns",
    "excess_kurtosis",
    "expected_maximum_sharpe",
    "expected_shortfall",
    "hit_rate",
    "horizon_return",
    "horizon_start",
    "is_horizon",
    "max_drawdown",
    "minimum_track_record",
    "monthly_returns",
    "months_before",
    "omega_ratio",
    "parametric_expected_shortfall",
    "parametric_var",
    "probabilistic_sharpe_ratio",
    "return_distribution",
    "rolling_beta",
    "rolling_correlation",
    "rolling_sharpe",
    "rolling_volatility",
    "sharpe_ratio",
    "sharpe_standard_error",
    "simple_returns",
    "skewness",
    "sortino_ratio",
    "source",
    "stability",
    "summarize",
    "tail_ratio",
    "turnover",
    "underwater",
    "value_at_risk",
    "wealth",
]
