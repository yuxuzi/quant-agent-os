"""Small numeric helpers every subpackage uses: finite values, and whether a series varies."""

from __future__ import annotations

import numpy as np

from quant_toolbox._types import FloatArray

FLAT = 1e-12  # relative deviation below which a series counts as constant


def finite(values: object) -> FloatArray:
    """The finite values of ``values``, flattened."""
    data = np.asarray(values, dtype=np.float64).ravel()
    result: FloatArray = data[np.isfinite(data)]
    return result


def varies(values: FloatArray) -> bool:
    """Whether ``values`` vary by more than floating-point noise."""
    if values.size < 2:
        return False
    deviation = float(np.std(values, ddof=1))
    return deviation > FLAT * max(1.0, abs(float(np.mean(values))))
