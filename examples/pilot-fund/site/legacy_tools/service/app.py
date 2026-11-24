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
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from kestrel_quant.risk_engine.engine import RiskEngine as LegacyRiskEngine
from pydantic import BaseModel

from quant_agent_os.caller_assertion import CALLER_HEADER, CallerAssertionError, verify_caller
from quant_agent_os.config.secrets import read_secret

# The platform signs each call's caller with this secret (stubs.py) and this service checks it. The
# secret is named by a file, KPC_RISK_CALLER_SECRET_FILE, or by the variable itself (decision 0030).
CALLER_SECRET = "KPC_RISK_CALLER_SECRET"
RISK_GROUP = "risk"  # the group access.toml gives the risk engine

OBJECTS: dict[str, Any] = {}
# One lock per legacy object: requests run on several threads, and the engine's cache and
# kestrel_quant's connection are not thread-safe.
LOCKS = {"risk_engine": threading.Lock()}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build each legacy object once: the slow set-up happens here, not per call."""
    # The firm's defaults: the latest warehouse day, CONFIG's 500-day lookback, and the
    # risk committee's scenarios.json. KPC_WAREHOUSE names the warehouse.
    secret = read_secret(CALLER_SECRET)
    if secret is None:
        raise RuntimeError(
            f"Set {CALLER_SECRET} or {CALLER_SECRET}_FILE: every call is checked with it."
        )
    OBJECTS["caller_secret"] = secret
    OBJECTS["risk_engine"] = LegacyRiskEngine()
    OBJECTS["risk_engine"].load()  # slow: reads the warehouse and the vendor model
    yield
    OBJECTS.clear()


app = FastAPI(title="kestrel_quant risk service", lifespan=lifespan)


def _admitted(tool: str) -> Any:
    """A dependency that refuses a call unless the platform vouched for a risk-group caller.

    A call with no assertion, or a forged, expired, or other tool's one, is 401. A verified caller
    outside the risk group is 403. Only the platform holds the secret, so only it can vouch.
    """

    def admit(
        assertion: Annotated[str | None, Header(alias=CALLER_HEADER)] = None,
    ) -> None:
        try:
            caller = verify_caller(assertion, OBJECTS["caller_secret"], tool=tool)
        except CallerAssertionError:
            raise HTTPException(401, "The caller is not verified.") from None
        if RISK_GROUP not in caller.groups:
            raise HTTPException(403, "Only the risk group reaches the risk engine.")

    return admit


class RiskDesksRequest(BaseModel):
    """The arguments of risk_desks."""

    pass


@app.post("/risk_desks", dependencies=[Depends(_admitted("risk_desks"))])
def risk_desks(request: RiskDesksRequest) -> Any:
    """The desks the engine holds a book for."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].desks(**arguments)
    return _json_ready(result)


class RiskExposureRequest(BaseModel):
    """The arguments of risk_exposure."""

    desk: str


@app.post("/risk_exposure", dependencies=[Depends(_admitted("risk_exposure"))])
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


@app.post("/risk_desk_var", dependencies=[Depends(_admitted("risk_desk_var"))])
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


@app.post("/risk_stress", dependencies=[Depends(_admitted("risk_stress"))])
def risk_stress(request: RiskStressRequest) -> Any:
    """A desk's P&L in USD under one scenario."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress(**arguments)
    return _json_ready(result)


class RiskStressAllRequest(BaseModel):
    """The arguments of risk_stress_all."""

    desk: str


@app.post("/risk_stress_all", dependencies=[Depends(_admitted("risk_stress_all"))])
def risk_stress_all(request: RiskStressAllRequest) -> Any:
    """A desk's P&L in USD under every scenario."""
    arguments = request.model_dump(exclude_none=True)  # None: the legacy default
    with LOCKS["risk_engine"]:
        result = OBJECTS["risk_engine"].stress_all(**arguments)
    return _json_ready(result)


class RiskFactorDecompositionRequest(BaseModel):
    """The arguments of risk_factor_decomposition."""

    desk: str


@app.post(
    "/risk_factor_decomposition", dependencies=[Depends(_admitted("risk_factor_decomposition"))]
)
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
