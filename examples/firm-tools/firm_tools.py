"""A firm's own code: plain Python, which knows nothing about the platform or MCP.

`firm_server.py` registers it as it is (decision 0024).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class DeskVar:
    """One desk's value at risk."""

    var: float
    periods: int
    warnings: list[str] = field(default_factory=list)


def desk_var(returns: list[float], confidence: float = 0.95) -> DeskVar:
    """One-period historical value at risk of one desk's returns: the nearest-rank loss.

    Args:
        returns: The desk's returns, one per period.
        confidence: For example 0.95 for 95%.
    """
    if not 0.5 < confidence < 1:
        raise ValueError(f"confidence must be between 0.5 and 1, such as 0.95, not {confidence}.")
    values = sorted(value for value in returns if not math.isnan(value))
    if not values:
        raise ValueError("The returns have no values.")
    # Rounded first: (1 - 0.95) * 20 is a little over 1 in floating point, and must be rank 1.
    rank = max(math.ceil(round((1 - confidence) * len(values), 9)), 1)
    return DeskVar(
        var=max(-values[rank - 1], 0.0),
        periods=len(values),
        warnings=["Fewer than 20 periods; read with care."] if len(values) < 20 else [],
    )
