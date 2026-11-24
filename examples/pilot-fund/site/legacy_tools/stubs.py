"""Typed stubs for the draft service over kestrel_quant, written by `qaos legacy wrap`.

Each stub's hints and docstring describe one endpoint of service/app.py; its body never
runs. `register` adds them to an app with FirmApp.call_service.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from quant_agent_os.registration import FirmApp

# The secret the service checks each call with, by name: the server reads it from the
# environment, or from the file KPC_RISK_CALLER_SECRET_FILE names (decision 0030). The name is
# repeated in service/app.py.
CALLER_SECRET = "KPC_RISK_CALLER_SECRET"

Desk = Literal["eq_ls", "macro", "commod"]
Scenario = Literal[
    "equity_crash_2008", "rates_shock_up_100bp", "usd_rally", "oil_supply_shock", "credit_event"
]


class Exposure(BaseModel):
    """A desk's book in USD on the engine's date."""

    gross: float
    net: float
    positions: int


class DeskVar(BaseModel):
    """A desk's value at risk."""

    desk: str
    as_of: str
    method: str
    confidence: float
    horizon_days: int
    var_usd: float
    var_pct_gross: float


class Stress(BaseModel):
    """A desk's P&L under one scenario, and its three worst positions."""

    desk: str
    scenario: str
    description: str
    pnl_usd: float
    worst_positions: dict[str, float]


class FactorDecomposition(BaseModel):
    """A desk's model risk from the vendor model, by factor."""

    desk: str
    as_of: str
    model_date: str
    vol_annual_usd: float
    exposures_usd: dict[str, float]
    variance_share: dict[str, float]
    specific_share: float


def risk_desks() -> list[str]:
    """The desks the risk engine holds a book for."""
    raise NotImplementedError  # never runs: the service answers


def risk_exposure(desk: Desk) -> Exposure:
    """A desk's gross and net market value in USD, and its number of positions.

    Args:
        desk: The desk.
    """
    raise NotImplementedError  # never runs: the service answers


def risk_desk_var(
    desk: Desk,
    confidence: float = 0.99,
    horizon: int = 1,
    method: Literal["historical", "parametric", "factor"] = "historical",
) -> DeskVar:
    """Value at risk in USD, a positive number: the loss not exceeded with ``confidence``.

    Args:
        desk: The desk.
        confidence: The confidence level, as a fraction: 0.95, 0.975, or 0.99.
        horizon: Days; one-day VaR is scaled by the square root of the days.
        method: historical (500 days of returns on today's book), parametric (EWMA
            covariance, half-life 60 days), or factor (the vendor model).
    """
    raise NotImplementedError  # never runs: the service answers


def risk_stress(desk: Desk, scenario: Scenario) -> Stress:
    """A desk's P&L in USD under one of the risk committee's scenarios.

    Args:
        desk: The desk.
        scenario: The scenario, from the risk engine's scenarios.json.
    """
    raise NotImplementedError  # never runs: the service answers


def risk_stress_all(desk: Desk) -> list[Stress]:
    """A desk's P&L in USD under every scenario, in scenario order.

    Args:
        desk: The desk.
    """
    raise NotImplementedError  # never runs: the service answers


def risk_factor_decomposition(desk: Desk) -> FactorDecomposition:
    """Each factor's share of the desk's model variance, and the specific share.

    Args:
        desk: The desk.
    """
    raise NotImplementedError  # never runs: the service answers


ENGINE = (
    "Kestrel's risk engine on the latest warehouse day: today's book, 500 days of returns, "
    "and the vendor model's latest month end; USD"
)


def register(app: FirmApp, service_url: str) -> None:
    """Register each endpoint of the service at ``service_url`` as a tool."""
    app.call_service(
        risk_desks,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_desks",
        convention=ENGINE,
    )
    app.call_service(
        risk_exposure,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_exposure",
        convention=ENGINE,
    )
    app.call_service(
        risk_desk_var,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_desk_var",
        convention=ENGINE + "; VaR a positive loss, square-root-of-time scaling",
    )
    app.call_service(
        risk_stress,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_stress",
        convention=ENGINE + "; factor shocks through the vendor loadings plus asset-class moves",
    )
    app.call_service(
        risk_stress_all,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_stress_all",
        convention=ENGINE + "; factor shocks through the vendor loadings plus asset-class moves",
    )
    app.call_service(
        risk_factor_decomposition,
        forward_caller=True,
        caller_secret_env=CALLER_SECRET,
        url=f"{service_url}/risk_factor_decomposition",
        convention=ENGINE + "; annual variance, shares sum to one with the specific share",
    )
