"""Your firm's own risk checks. This file is yours to change.

The limits in ``config.toml`` cover the common cases. For anything else, add a function that takes
the `RiskView` and returns `Check` results, and list it in ``CHECKS``. Each appears in the report,
and a breach makes ``check`` exit 1. For example::

    from ..risk import Check, RiskView


    def no_shorts_in_restricted(view: RiskView) -> list[Check]:
        \"\"\"Never short an asset on the restricted list.\"\"\"
        restricted = {"ASSET03"}
        worst = min(
            (w for a, w in zip(view.assets, view.weights) if a in restricted), default=0.0
        )
        return [Check("Restricted shorts", float(worst), 0.0, worst < 0.0)]


    CHECKS = {"restricted_shorts": no_shorts_in_restricted}
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CHECKS: dict[str, Callable[[Any], list[Any]]] = {}
