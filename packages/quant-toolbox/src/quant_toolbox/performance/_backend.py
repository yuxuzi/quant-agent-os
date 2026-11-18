"""Which backend computes the metrics both implement: empyrical when installed, otherwise numpy.

``QUANT_TOOLBOX_BACKEND`` (``auto``, ``empyrical``, or ``numpy``), or a function's ``backend``
argument, chooses. ``auto`` is the default. Both backends give the same answer to within 1e-12,
which `tests/toolbox/test_backends.py` checks on clean data and on the edge cases where empyrical
alone would not (constant series, no downside, gaps).
"""

from __future__ import annotations

import functools
import os
from importlib.metadata import PackageNotFoundError, version
from types import ModuleType

from quant_toolbox._optional import require

BACKEND_VARIABLE = "QUANT_TOOLBOX_BACKEND"
BACKENDS = ("auto", "empyrical", "numpy")
EMPYRICAL_SOURCE = "empyrical-reloaded (the metrics behind pyfolio)"
NUMPY_SOURCE = "numpy: Quant Toolbox's own formulas, held to empyrical-reloaded's by tests"


def backend(choice: str | None = None) -> str:
    """The backend a call uses: ``"empyrical"`` or ``"numpy"``.

    ``choice`` overrides ``QUANT_TOOLBOX_BACKEND``; ``auto`` picks empyrical when it imports.
    """
    chosen = (choice or os.environ.get(BACKEND_VARIABLE) or "auto").strip().lower()
    if chosen not in BACKENDS:
        raise ValueError(
            f"Unknown metrics backend {chosen!r}; use {', '.join(BACKENDS)} "
            f"(argument backend, or {BACKEND_VARIABLE})."
        )
    if chosen == "numpy":
        return "numpy"
    if chosen == "empyrical":
        require("empyrical", extra="empyrical", purpose="the empyrical backend")
        return "empyrical"
    return "empyrical" if _empyrical_imports() else "numpy"


def source(choice: str | None = None) -> str:
    """What computed the metrics, for a report's conventions or a run's provenance."""
    return EMPYRICAL_SOURCE if backend(choice) == "empyrical" else NUMPY_SOURCE


def backend_version(choice: str | None = None) -> str:
    """The backend and its version, such as ``empyrical-reloaded 0.7.0`` or ``numpy``."""
    if backend(choice) == "numpy":
        return "numpy"
    try:
        return f"empyrical-reloaded {version('empyrical-reloaded')}"
    except PackageNotFoundError:  # pragma: no cover - installed under another distribution name
        return "empyrical-reloaded"


def implementation(choice: str | None = None) -> ModuleType:
    """The module holding the chosen backend's functions."""
    if backend(choice) == "empyrical":
        from quant_toolbox.performance import _empyrical

        return _empyrical
    from quant_toolbox.performance import _numpy

    return _numpy


@functools.cache
def _empyrical_imports() -> bool:
    try:
        import empyrical  # noqa: F401
    except Exception:  # an install broken in any way counts as not installed
        return False
    return True
