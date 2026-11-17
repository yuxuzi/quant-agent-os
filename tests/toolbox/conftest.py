"""Every toolbox test runs under both metrics backends, empyrical and numpy, when both exist."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from quant_toolbox._optional import installed
from quant_toolbox.performance import BACKEND_VARIABLE

BACKENDS = ["numpy", "empyrical"] if installed("empyrical") else ["numpy"]


@pytest.fixture(autouse=True, params=BACKENDS)
def metrics_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """The backend this run of the test uses."""
    monkeypatch.setenv(BACKEND_VARIABLE, request.param)
    yield request.param
