"""Sablecroft Alternatives' own server: the platform's toolsets, the fund's Databricks warehouse, and
its own tools, under the fund's Entra ID sign-in and entitlements (decisions 0020, 0024, 0028).

Registration is explicit: this file names every tool the fund serves. Keys, sign-in, the run store,
permissions, and the access file come from the usual settings. The warehouse is read through
`SablecroftStore` over a SQL warehouse.

Usage (production):  SAB_WAREHOUSE_KIND=databricks python sablecroft_server.py
Usage (local):       SAB_LAKE_ROOT=/path/to/built/lake python sablecroft_server.py
                     SAB_LAKE_ROOT=... python sablecroft_server.py acceptance sablecroft_acceptance.toml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

import sablecroft_tools as tools  # noqa: E402
from sablecroft_store import SablecroftStore, connect  # noqa: E402

from quant_agent_os.api.server import DEFAULT_HOST  # noqa: E402
from quant_agent_os.config import get_settings  # noqa: E402
from quant_agent_os.data import DatasetStore  # noqa: E402
from quant_agent_os.registration import FirmApp, Frame, Series  # noqa: E402
from quant_agent_os.tools import AccessPolicy, FileAccessPolicy, PermissionPolicy  # noqa: E402
from quant_platform import Catalog, compose, serve_apps  # noqa: E402
from quant_platform.acceptance import GoldFileError, load_gold, run_acceptance  # noqa: E402

app = FirmApp(
    "sab",
    prefix=True,
    instructions=(
        "Sablecroft's own tools are prefixed sab_. Private-fund figures (IRR, TVPI, PME) take one fund's "
        "column of pm_cashflows and pm_nav, and the index in the fund's currency (idx_public SCI_US for USD "
        "funds, SCI_EU for EUR funds). Hedge-fund returns are monthly: ret_final is lagged 15 to 45 days "
        "and ret_est is not; say which vintage a number uses. Unsmoothed volatility and the Lo Sharpe "
        "are estimates, not measurements."
    ),
)
app.expose(
    tools.private_fund_returns,
    name="private_fund_returns",
    cashflows=Series,
    nav=Series,
    convention="XIRR actual/365 on signed flows with NAV as a terminal inflow; TVPI = (distributions + "
    "NAV) / paid-in; DPI = distributions / paid-in; RVPI = NAV / paid-in; the fund's currency",
)
app.expose(
    tools.private_fund_pme,
    name="private_fund_pme",
    cashflows=Series,
    nav=Series,
    index=Series,
    convention="Kaplan-Schoar PME = (sum D * I_T/I_t + NAV) / sum C * I_T/I_t; Long-Nickels index IRR = "
    "XIRR with terminal value of the index units the flows would have bought; index on the last day "
    "on or before each date",
)
app.expose(
    tools.unsmooth_returns,
    name="unsmooth_returns",
    returns=Series,
    convention="Simplified Getmansky-Lo-Makarov MA(2): method of moments on lag-1 and lag-2 "
    "autocorrelations over non-increasing weights summing to 1; unsmoothed vol = reported / "
    "sqrt(sum of squared weights)",
)
app.expose(
    tools.lo_adjusted_sharpe,
    name="lo_adjusted_sharpe",
    returns=Series,
    convention="Lo (2002): annual Sharpe = per-period Sharpe * q / sqrt(q + 2 sum (q-k) rho_k)",
)
app.expose(
    tools.liquidity_ladder,
    name="liquidity_ladder",
    terms=Frame,
    convention="Request at the later of as-of and lock-up end; dealing at month/quarter/year-end after "
    "notice; investor-level gate per dealing date on today's position; cash 30 days after dealing",
)
app.expose(
    tools.manager_peer_ranking,
    name="manager_peer_ranking",
    returns=Frame,
    terms=Frame,
    convention="Trailing Sharpe (geometric annual return / annual vol, zero risk-free) over the last "
    "window months with at least min_months returns; rank within strategy, 1 = best",
)
app.expose(
    tools.revision_bias,
    name="revision_bias",
    estimates=Frame,
    finals=Frame,
    convention="revision = final - estimate over months with both; t = mean / (sd / sqrt(n)); flagged "
    "when |t| > threshold and n >= 12",
)


def warehouse() -> SablecroftStore:
    """Return the fund's dataset store over its configured SQL warehouse."""
    return SablecroftStore(connect())


def build_catalog(
    store: DatasetStore, policy: PermissionPolicy, access: AccessPolicy | None = None
) -> Catalog:
    """Return what the fund serves, as `serve_apps` composes it."""
    return compose(policy=policy, datasets=store, access=access, apps=[app])


def serve() -> None:
    """Serve until interrupted, on ``SAB_HOST``:``SAB_PORT`` (loopback, 8000 by default)."""
    settings = get_settings()
    access = FileAccessPolicy(settings.access_file) if settings.access_file else None
    serve_apps(
        [app],
        datasets=warehouse(),
        access=access,
        host=os.environ.get("SAB_HOST", DEFAULT_HOST),
        port=int(os.environ.get("SAB_PORT", "8000")),
    )


def acceptance(cases: Path) -> int:
    """Run the agreed cases as an operator; return 0 when all pass, 1 when any fails, 2 on a bad file."""
    try:
        gold, digest = load_gold(cases)
    except GoldFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    report = run_acceptance(build_catalog(warehouse(), get_settings().policy()), gold, digest)
    sys.stdout.write(report.markdown())
    return 0 if report.passed else 1


def main(argv: list[str]) -> int:
    """Serve, or run the acceptance cases with ``acceptance CASES``."""
    if argv[:1] == ["acceptance"] and len(argv) == 2:
        return acceptance(Path(argv[1]))
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
