"""kestrel_quant as tools: a draft written by `qaos legacy wrap`; edit it and commit it.

Every tool is named here, in the firm's own code (decisions 0020 and 0024); nothing is
discovered when the server starts. The fund serves these apps from kestrel_server.py,
beside the platform's toolsets.
"""

from __future__ import annotations

import os

import facades
import stubs

from quant_agent_os.registration import FirmApp, Frame, Series

# Where service/app.py runs: loopback on the server's own machine.
SERVICE_URL = os.environ.get("KPC_RISK_SERVICE_URL", "http://127.0.0.1:8100")


def build_app() -> FirmApp:
    """Return the fund's legacy functions as tools, each through its facade."""
    app = FirmApp(
        "kestrel",
        instructions="kpc_* tools run Kestrel's own legacy code, the figures the morning "
        "meeting uses. Prefer kpc_desk_pnl for a desk's P&L and quote its numbers as computed.",
    )
    app.expose(
        facades.kpc_backtest,
        weights=Frame,
        returns=Frame,
        convention="Close-to-close; weights applied with a one-day lag; costs on one-way turnover",
    )
    app.expose(
        facades.kpc_drawdown_table,
        returns=Series,
        convention="Drawdown from the running peak of compounded wealth, a negative fraction",
    )
    app.expose(
        facades.kpc_desk_pnl,
        convention="USD; yesterday's quantities times today's price change, less commissions",
    )
    app.expose(
        facades.kpc_summary,
        returns=Series,
        convention="Arithmetic mean times 252; sample volatility times sqrt(252); "
        "no risk-free rate",
    )
    app.expose(
        facades.kpc_momentum_signal,
        prices=Frame,
        convention="Price return from t-126 to t-21 days, z-scored across names on each date",
    )
    app.expose(
        facades.kpc_carry_signal,
        convention="The warehouse's carry, z-scored across names on each date, clipped at 3",
    )
    app.expose(
        facades.kpc_value_zscore,
        values=Frame,
        convention="Book-to-price z-scored within each region on each date",
    )
    return app


def build_risk_app(service_url: str = SERVICE_URL) -> FirmApp:
    """Return the risk engine's service endpoints as tools."""
    app = FirmApp(
        "kestrel_risk",
        instructions="risk_* tools ask Kestrel's risk engine, which holds today's books and the "
        "vendor model in memory. Prefer them for the risk committee's VaR and stress figures.",
    )
    stubs.register(app, service_url)
    return app


def build_apps(service_url: str = SERVICE_URL) -> list[FirmApp]:
    """Return both apps, as the fund serves them."""
    return [build_app(), build_risk_app(service_url)]
