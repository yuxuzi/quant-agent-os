"""Typed stubs for the draft service over kestrel_quant, written by `qaos legacy wrap`.

Each stub's hints and docstring describe one endpoint of service/app.py; its body never
runs. `register` adds them to an app with FirmApp.call_service.
"""

from __future__ import annotations

from typing import Any

from quant_agent_os.registration import FirmApp


# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_desks() -> Any:
    """TODO(doc): say what risk_desks returns, in one sentence; clients read it."""
    raise NotImplementedError  # never runs: the service answers


# TODO(type): desk's hint is Any; name its type.
# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_exposure(desk: Any) -> Any:
    """TODO(doc): say what risk_exposure returns, in one sentence; clients read it.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
    """
    raise NotImplementedError  # never runs: the service answers


# TODO(type): desk's hint is Any; name its type.
# TODO(type): confidence's hint is Any; name its type.
# TODO(type): horizon's hint guessed from its default, is int; name its type.
# TODO(type): method's hint guessed from its default, is str; name its type.
# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_desk_var(
    desk: Any,
    confidence: Any = None,
    horizon: int = 1,
    method: str = "historical",
) -> Any:
    """Value at risk in USD, a positive number: the loss not exceeded with ``confidence``.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
        confidence: TODO(unit): what it holds, and in which unit.
        horizon: TODO(unit): what it holds, and in which unit.
        method: TODO(unit): what it holds, and in which unit.
    """
    raise NotImplementedError  # never runs: the service answers


# TODO(type): desk's hint is Any; name its type.
# TODO(type): scenario's hint is Any; name its type.
# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_stress(desk: Any, scenario: Any) -> Any:
    """TODO(doc): say what risk_stress returns, in one sentence; clients read it.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
        scenario: TODO(unit): what it holds, and in which unit.
    """
    raise NotImplementedError  # never runs: the service answers


# TODO(type): desk's hint is Any; name its type.
# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_stress_all(desk: Any) -> Any:
    """TODO(doc): say what risk_stress_all returns, in one sentence; clients read it.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
    """
    raise NotImplementedError  # never runs: the service answers


# TODO(type): desk's hint is Any; name its type.
# TODO(type): name the return type, so the answer is checked; Any accepts anything.
def risk_factor_decomposition(desk: Any) -> Any:
    """Each factor's share of the desk's model variance, and the specific share.

    Args:
        desk: TODO(unit): what it holds, and in which unit.
    """
    raise NotImplementedError  # never runs: the service answers


def register(app: FirmApp, service_url: str) -> None:
    """Register each endpoint of the service at ``service_url`` as a tool."""
    # TODO(convention): how risk_desks's result is defined (method, window, units).
    app.call_service(risk_desks, url=f"{service_url}/risk_desks")
    # TODO(convention): how risk_exposure's result is defined (method, window, units).
    app.call_service(risk_exposure, url=f"{service_url}/risk_exposure")
    # TODO(convention): how risk_desk_var's result is defined (method, window, units).
    app.call_service(risk_desk_var, url=f"{service_url}/risk_desk_var")
    # TODO(convention): how risk_stress's result is defined (method, window, units).
    app.call_service(risk_stress, url=f"{service_url}/risk_stress")
    # TODO(convention): how risk_stress_all's result is defined (method, window,
    #   units).
    app.call_service(risk_stress_all, url=f"{service_url}/risk_stress_all")
    # TODO(convention): how risk_factor_decomposition's result is defined (method,
    #   window, units).
    app.call_service(
        risk_factor_decomposition,
        url=f"{service_url}/risk_factor_decomposition",
    )
