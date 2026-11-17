"""Quant Toolbox: the tables, metrics, risk, research, report, and audit code quant apps share.

Quant Analytics, the App Factory's template packs, and the applications they generate all import
it, so a formula, a table reader, or a report renderer exists once. It is MIT licensed, and it
imports nothing of the platform.

Seven subpackages, in layers (a layer imports only the ones before it):

1. `quant_toolbox.data`: tables, price panels, configuration files, data checks, synthetic data.
2. `quant_toolbox.performance`: return metrics, horizons, and drawdowns.
3. `quant_toolbox.risk` and `quant_toolbox.research`: risk measures and decomposition; signal
   evaluation, portfolio weights, and the backtest engine.
4. `quant_toolbox.report` and `quant_toolbox.audit`: report documents, charts, and renderers;
   provenance and run folders.
5. `quant_toolbox.testing`: bias checks a project's tests call.

Importing this package imports none of them; each loads on first use. Only numpy is needed to
import any subpackage. pygal, plotly, openpyxl, pandas, and empyrical are optional, loaded by the
function that uses them; `features` reports which are installed.
"""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from quant_toolbox._optional import MissingExtraError

try:
    __version__ = version("quant-agent-os")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
    __version__ = "0.0.0"

SUBPACKAGES = ("data", "performance", "risk", "research", "report", "audit", "testing")
__all__ = [
    "SUBPACKAGES",
    "MissingExtraError",
    "__version__",
    "audit",
    "data",
    "features",
    "performance",
    "report",
    "research",
    "risk",
    "testing",
]

if TYPE_CHECKING:
    from quant_toolbox import audit, data, performance, report, research, risk, testing


def __getattr__(name: str) -> Any:
    """Load a subpackage on first attribute access, so ``import quant_toolbox`` stays cheap."""
    if name in SUBPACKAGES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


def features() -> dict[str, bool]:
    """Which optional parts are installed: charts, interactive charts, Excel, pandas, empyrical."""
    from quant_toolbox._optional import installed

    return {
        "static_charts": installed("pygal"),
        "interactive_charts": installed("plotly"),
        "excel": installed("openpyxl"),
        "pandas": installed("pandas"),
        "empyrical": installed("empyrical"),
    }
