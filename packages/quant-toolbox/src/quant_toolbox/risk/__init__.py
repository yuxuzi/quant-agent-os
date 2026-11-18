"""Risk: tail measures, exposures, concentration, liquidity, Euler contributions, factors, stress.

Value at risk and expected shortfall come from `quant_toolbox.performance` and are re-exported
here. Every decomposition is an exact identity, checked by known-answer tests: contributions sum
to volatility, risk shares to one, and factor plus specific variance shares to one.

Also here: liquidity by Form PF bucket, historical scenario windows, risk under a vendor's factor
model (`factor_risk`), returns-based style regression with HAC t-statistics, correlation
summaries, expected-shortfall contributions, and EWMA covariance for `euler_contributions`.
"""

from quant_toolbox.performance import expected_shortfall, value_at_risk
from quant_toolbox.risk._book import (
    UNGROUPED,
    Exposures,
    exposures,
    group_exposures,
    risk_shares,
    simulated_returns,
    stress_return,
)
from quant_toolbox.risk._conventions import CONVENTIONS, FACTOR_CONVENTIONS, MEASURE_CONVENTIONS
from quant_toolbox.risk._decomposition import (
    FLAT,
    Contribution,
    FactorBreakdown,
    VolatilityBreakdown,
    euler_contributions,
    factor_exposures,
    volatility_contributions,
)
from quant_toolbox.risk._factor_model import FactorRisk, factor_risk
from quant_toolbox.risk._liquidity import LIQUIDITY_BUCKETS, NO_ADV, Liquidity, liquidity
from quant_toolbox.risk._portfolio import (
    EWMA_DECAY,
    CorrelationSummary,
    correlation_summary,
    ewma_covariance,
    expected_shortfall_contributions,
)
from quant_toolbox.risk._scenarios import SCENARIOS, historical_stress, scenario_shocks
from quant_toolbox.risk._style import StyleRegression, style_regression

__all__ = [
    "CONVENTIONS",
    "EWMA_DECAY",
    "FACTOR_CONVENTIONS",
    "FLAT",
    "LIQUIDITY_BUCKETS",
    "MEASURE_CONVENTIONS",
    "NO_ADV",
    "SCENARIOS",
    "UNGROUPED",
    "Contribution",
    "CorrelationSummary",
    "Exposures",
    "FactorBreakdown",
    "FactorRisk",
    "Liquidity",
    "StyleRegression",
    "VolatilityBreakdown",
    "correlation_summary",
    "euler_contributions",
    "ewma_covariance",
    "expected_shortfall",
    "expected_shortfall_contributions",
    "exposures",
    "factor_exposures",
    "factor_risk",
    "group_exposures",
    "historical_stress",
    "liquidity",
    "risk_shares",
    "scenario_shocks",
    "simulated_returns",
    "stress_return",
    "style_regression",
    "value_at_risk",
    "volatility_contributions",
]
