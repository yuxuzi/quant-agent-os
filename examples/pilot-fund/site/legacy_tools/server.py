"""kestrel_quant as tools: a draft written by `qaos legacy wrap`; edit it and commit it.

Every tool is named here, in the firm's own code (decisions 0020 and 0024); nothing is
discovered when the server starts. Serve it with:

    uv run python server.py
"""

from __future__ import annotations

from pathlib import Path

from quant_agent_os.registration import FirmApp, Frame, Series

import facades
import stubs

# TODO(review): where service/app.py runs; use https off this machine.
SERVICE_URL = "http://127.0.0.1:8100"


def build_app(service_url: str = SERVICE_URL) -> FirmApp:
    """Return the app, with each accepted entry registered by name."""
    app = FirmApp(
        "kestrel",
        instructions=None,  # TODO(doc): tell clients when to prefer these tools
    )
    # TODO(convention): how kpc_backtest's figure is defined (window, annualisation,
    #   units).
    app.expose(facades.kpc_backtest, weights=Frame, returns=Frame)
    # TODO(convention): how kpc_drawdown_table's figure is defined (window,
    #   annualisation, units).
    app.expose(facades.kpc_drawdown_table, returns=Series)
    # TODO(convention): how kpc_desk_pnl's figure is defined (window, annualisation,
    #   units).
    app.expose(facades.kpc_desk_pnl)
    app.expose(
        facades.kpc_summary,
        returns=Series,
        convention='Arithmetic mean times 252; sample volatility times sqrt(252); no risk-free rate',
    )
    # TODO(convention): how kpc_momentum_signal's figure is defined (window,
    #   annualisation, units).
    app.expose(facades.kpc_momentum_signal, prices=Frame)
    # TODO(convention): how kpc_carry_signal's figure is defined (window,
    #   annualisation, units).
    app.expose(facades.kpc_carry_signal)
    # TODO(convention): how kpc_value_zscore's figure is defined (window,
    #   annualisation, units).
    app.expose(facades.kpc_value_zscore, values=Frame)
    stubs.register(app, service_url)
    return app


def main() -> None:
    """Serve the app beside the platform's toolsets, with the usual settings."""
    from quant_agent_os.data import DirectoryDatasetStore
    from quant_platform import serve_apps

    # TODO(review): serve the firm's own DatasetStore; CSV files in a directory are
    #   the simplest.
    serve_apps([build_app()], datasets=DirectoryDatasetStore(Path("datasets")))


if __name__ == "__main__":
    main()
