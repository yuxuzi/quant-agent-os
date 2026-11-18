"""Risk: tail measures, a book's exposures and concentration, Euler contributions, factors, stress.

Value at risk and expected shortfall come from `quant_toolbox.performance` and are re-exported
here. Every decomposition is an exact identity, checked by known-answer tests: contributions sum
to volatility, risk shares to one, and factor plus specific variance shares to one.
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
from quant_toolbox.risk._conventions import CONVENTIONS, FACTOR_CONVENTIONS
from quant_toolbox.risk._decomposition import (
    FLAT,
    Contribution,
    FactorBreakdown,
    VolatilityBreakdown,
    factor_exposures,
    volatility_contributions,
)

__all__ = [
    "CONVENTIONS",
    "FACTOR_CONVENTIONS",
    "FLAT",
    "UNGROUPED",
    "Contribution",
    "Exposures",
    "FactorBreakdown",
    "VolatilityBreakdown",
    "expected_shortfall",
    "exposures",
    "factor_exposures",
    "group_exposures",
    "risk_shares",
    "simulated_returns",
    "stress_return",
    "value_at_risk",
    "volatility_contributions",
]
