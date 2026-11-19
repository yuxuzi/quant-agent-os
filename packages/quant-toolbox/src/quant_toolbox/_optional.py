"""Optional libraries: imported by the function that needs them, with an error naming the extra."""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

# The quant-toolbox extra that brings each optional library, by the name it is imported as.
EXTRAS: dict[str, str] = {
    "pygal": "charts",
    "plotly": "interactive",
    "openpyxl": "excel",
    "empyrical": "empyrical",
    "pandas": "pandas",
    "sqlalchemy": "sql",
}


class MissingExtraError(ImportError):
    """Raised when a function needs an optional library that is not installed."""


def require(module: str, *, extra: str | None = None, purpose: str = "") -> ModuleType:
    """Import ``module``, or raise `MissingExtraError` saying which extra installs it."""
    try:
        return importlib.import_module(module)
    except ImportError as error:
        root = module.split(".")[0]
        chosen = extra or EXTRAS.get(root, "all")
        needed = f" for {purpose}" if purpose else ""
        raise MissingExtraError(
            f"{module} is needed{needed} and is not installed. Install the quant-toolbox extra "
            f'that brings it: `uv add "quant-toolbox[{chosen}]"`.'
        ) from error


def installed(module: str) -> bool:
    """Whether ``module`` can be imported, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False
