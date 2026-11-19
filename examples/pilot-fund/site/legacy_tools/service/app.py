"""A draft service for stateful code in kestrel_quant, written by `qaos legacy wrap`.

It builds each legacy object once, when it starts, and answers read-only POST requests;
stubs.py registers each endpoint as a tool with FirmApp.call_service. Run it with:

    uv run uvicorn service.app:app --host 127.0.0.1 --port 8100
"""

from __future__ import annotations

import dataclasses
import math
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from kestrel_quant.risk_engine.engine import RiskEngine as LegacyRiskEngine
from pydantic import BaseModel

OBJECTS: dict[str, Any] = {}
# One lock per legacy object: requests run on several threads, and the engine's cache and
# kestrel_quant's connection are not thread-safe.
LOCKS = {"risk_engine": threading.Lock()}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build each legacy object once: the slow set-up happens here, not per call."""
    # The firm's defaults: the latest warehouse day, CONFIG's 500-day lookback, and the
    # risk committee's scenarios.json. KPC_WAREHOUSE names the warehouse.
    OBJECTS["risk_engine"] = LegacyRiskEngine()
    OBJECTS["risk_engine"].load()  # slow: reads the warehouse and the vendor model
    yield
    OBJECTS.clear()


app = FastAPI(title="kestrel_quant risk service", lifespan=lifespan)


class RiskDesksRequest(BaseModel):
    """The arguments of risk_desks."""

    pass


@app.post("/risk_desks")
def risk_desks(request: RiskDesksRequest) -> Any:
    """The desks the engine holds a book for."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].desks(**arguments)
    return _json_ready(result)


class RiskExposureRequest(BaseModel):
    """The arguments of risk_exposure."""

    desk: str


@app.post("/risk_exposure")
def risk_exposure(request: RiskExposureRequest) -> Any:
    """A desk's gross and net market value in USD, and its positions."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].exposure(**arguments)
    return _json_ready(result)


class RiskDeskVarRequest(BaseModel):
    """The arguments of risk_desk_var."""

    desk: str
    confidence: float | None = None
    horizon: int = 1
    method: str = "historical"


@app.post("/risk_desk_var")
def risk_desk_var(request: RiskDeskVarRequest) -> Any:
    """Value at risk in USD, a positive number: the loss not exceeded with ``confidence``."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].desk_var(**arguments)
    return _json_ready(result)


class RiskStressRequest(BaseModel):
    """The arguments of risk_stress."""

    desk: str
    scenario: str


@app.post("/risk_stress")
def risk_stress(request: RiskStressRequest) -> Any:
    """A desk's P&L in USD under one scenario."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress(**arguments)
    return _json_ready(result)


class RiskStressAllRequest(BaseModel):
    """The arguments of risk_stress_all."""

    desk: str


@app.post("/risk_stress_all")
def risk_stress_all(request: RiskStressAllRequest) -> Any:
    """A desk's P&L in USD under every scenario."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress_all(**arguments)
    return _json_ready(result)


class RiskFactorDecompositionRequest(BaseModel):
    """The arguments of risk_factor_decomposition."""

    desk: str


@app.post("/risk_factor_decomposition")
def risk_factor_decomposition(request: RiskFactorDecompositionRequest) -> Any:
    """Each factor's share of the desk's model variance, and the specific share."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].factor_decomposition(**arguments)
    return _json_ready(result)


def _json_ready(value: Any) -> Any:
    """The legacy result as JSON values: tables and arrays as lists, missing values as null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_ready(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if hasattr(value, "columns") and hasattr(value, "index"):  # a pandas DataFrame
        return {
            "index": [_key(key) for key in value.index],
            "columns": {str(name): _json_ready(value[name].tolist()) for name in value.columns},
        }
    if hasattr(value, "items") and hasattr(value, "index"):  # a pandas Series
        return {_key(key): _json_ready(item) for key, item in value.items()}
    if hasattr(value, "tolist"):  # a NumPy array or scalar
        return _json_ready(value.tolist())
    if hasattr(value, "model_dump") and not isinstance(value, type):  # a Pydantic model
        return _json_ready(value.model_dump())
    if hasattr(value, "isoformat"):  # a date or time
        return _key(value)
    if value is None or isinstance(value, str | int):
        return value
    raise TypeError(
        f"The legacy result holds a {type(value).__name__}, which JSON cannot carry; "
        "convert it to plain values in the facade."
    )


def _key(key: Any) -> str:
    if hasattr(key, "isoformat"):
        return str(key.isoformat()).removesuffix("T00:00:00")
    return str(key)
