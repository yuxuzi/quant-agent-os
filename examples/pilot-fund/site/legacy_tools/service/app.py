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
# One lock per legacy object: requests run on several threads, and legacy code is
# rarely thread-safe.
# TODO(review): drop a lock only for code known to be thread-safe.
LOCKS = {"risk_engine": threading.Lock()}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build each legacy object once: the slow set-up happens here, not per call."""
    # TODO(config): RiskEngine is built with its defaults for as_of, lookback,
    #   scenarios; set the firm's values.
    OBJECTS["risk_engine"] = LegacyRiskEngine()
    # TODO(config): RiskEngine.load() looks like set-up, and it is not served, so the
    #   draft calls it once here; delete the call if the object needs none, or add the
    #   firm's arguments.
    OBJECTS["risk_engine"].load()
    yield
    OBJECTS.clear()


app = FastAPI(title="kestrel_quant service (draft)", lifespan=lifespan)


class RiskDesksRequest(BaseModel):
    """The arguments of risk_desks."""

    pass


@app.post("/risk_desks")
def risk_desks(request: RiskDesksRequest) -> Any:
    """TODO(doc): what risk_desks returns."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].desks(**arguments)
    return _json_ready(result)


class RiskExposureRequest(BaseModel):
    """The arguments of risk_exposure."""

    desk: Any


@app.post("/risk_exposure")
def risk_exposure(request: RiskExposureRequest) -> Any:
    """TODO(doc): what risk_exposure returns."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].exposure(**arguments)
    return _json_ready(result)


class RiskDeskVarRequest(BaseModel):
    """The arguments of risk_desk_var."""

    desk: Any
    confidence: Any = None
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

    desk: Any
    scenario: Any


@app.post("/risk_stress")
def risk_stress(request: RiskStressRequest) -> Any:
    """TODO(doc): what risk_stress returns."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress(**arguments)
    return _json_ready(result)


class RiskStressAllRequest(BaseModel):
    """The arguments of risk_stress_all."""

    desk: Any


@app.post("/risk_stress_all")
def risk_stress_all(request: RiskStressAllRequest) -> Any:
    """TODO(doc): what risk_stress_all returns."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress_all(**arguments)
    return _json_ready(result)


class RiskFactorDecompositionRequest(BaseModel):
    """The arguments of risk_factor_decomposition."""

    desk: Any


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
