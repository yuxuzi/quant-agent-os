"""Wrenfield Credit Opportunities' own server: the platform's toolsets, the fund's credit tools, and
its estate (S3 lake through DuckDB, RDS PostgreSQL) under the fund's sign-in and entitlements
(decisions 0020, 0024, and 0028).

Registration is explicit: this file names every tool the fund serves. Keys, sign-in, the run
store, permissions, and the access file come from the usual settings (see deploy/wrenfield.env).

Usage:
  WRENFIELD_ESTATE=/data/estate python wrenfield_server.py                       # serve
  WRENFIELD_ESTATE=/data/estate python wrenfield_server.py acceptance cases.toml # operator check
"""

from __future__ import annotations

import functools
import os
import sys
from contextlib import ExitStack
from pathlib import Path

SITE = Path(__file__).resolve().parent
for _path in (SITE,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import wrenfield_credit as credit  # noqa: E402
from wrenfield_store import WrenfieldDatasetStore  # noqa: E402

from quant_agent_os.api.server import DEFAULT_HOST  # noqa: E402
from quant_agent_os.config import get_settings  # noqa: E402
from quant_agent_os.data import DatasetStore  # noqa: E402
from quant_agent_os.registration import FirmApp, Frame, Series  # noqa: E402
from quant_agent_os.tools import AccessPolicy, FileAccessPolicy, PermissionPolicy  # noqa: E402
from quant_platform import Catalog, compose, serve_apps  # noqa: E402
from quant_platform.acceptance import GoldFileError, load_gold, run_acceptance  # noqa: E402
from quant_research import open_research  # noqa: E402
from quant_research.application.retrieval import ResearchService  # noqa: E402
from quant_research.infrastructure.postgres_store import is_postgres_url  # noqa: E402


@functools.cache
def build_app() -> FirmApp:
    """Return the fund's credit tools, each with the convention it states (built once: schemas
    for seven tools take over a second to build)."""
    app = FirmApp(
        "wrenfield",
        prefix=True,
        instructions=(
            "Wrenfield Credit Opportunities. Exposures are USD market values, signed. Use "
            "spread_duration_dts for spread risk, default_stress for jump to default, and "
            "cs01_dv01_by_tenor for sensitivities; report the numbers as computed. Pass a "
            "book's own datasets (mv_hy_ls, w_hy_ls, ...) or mv_fund for the whole fund."
        ),
    )
    app.expose(
        credit.spread_duration_dts,
        mv=Frame, sprdur=Frame, oas=Frame, static=Frame, rating=Frame, capital=Series,
        returns=Frame,
        convention="DTS = sum of (market value / capital) x spread duration x OAS in bp; defaulted "
        "bonds left out",
    )  # fmt: skip
    app.expose(
        credit.cs01_dv01_by_tenor,
        mv=Frame, sprdur=Frame, moddur=Frame, ttm=Frame, cds_protection=Frame, cds_5y=Frame,
        convention="P&L in USD per 1 bp; bonds by spread/modified duration, CDS by 5-year risky "
        "annuity; buckets 0-2y, 2-5y, 5-10y, 10y+",
    )  # fmt: skip
    app.expose(
        credit.default_stress,
        mv=Frame, px_clean=Frame, px_accrued=Frame, oas=Frame, static=Frame, cds_protection=Frame,
        capital=Series,
        convention="Loss = face x recovery - dirty market value; protection pays notional x 60%",
    )  # fmt: skip
    app.expose(
        credit.spread_shock_stress,
        mv=Frame, sprdur=Frame, rating=Frame, cds_protection=Frame, capital=Series,
        convention="First order: -mv x spread duration x shock; protection at a 300 bp annuity",
    )  # fmt: skip
    app.expose(
        credit.bond_cds_basis,
        oas=Frame, ttm=Frame, cds_3y=Frame, cds_5y=Frame, cds_10y=Frame,
        convention="CDS at the bond's maturity (linear in 3Y, 5Y, 10Y) minus OAS, in bp",
    )  # fmt: skip
    app.expose(
        credit.issuer_sector_concentration,
        mv=Frame, static=Frame, capital=Series,
        convention="Percent of capital; Herfindahl of gross issuer shares",
    )  # fmt: skip
    app.expose(
        credit.rating_migration,
        scores=Frame,
        convention="Every 12-month window of month-end S&P scores; AA, A, BBB, BB, B, CCC, D",
    )  # fmt: skip
    return app


def estate() -> WrenfieldDatasetStore:
    """Return the fund's estate, rooted at ``WRENFIELD_ESTATE`` (the URI map and the stand-ins).

    On AWS the lake is read at its ``s3://`` URI and the database at ``WRENFIELD_OMS_URL``, whose
    password ECS injects from Secrets Manager as ``WRENFIELD_OMS_PASSWORD``.
    """
    path = os.environ.get("WRENFIELD_ESTATE")
    if not path or not (Path(path) / "uri_map.json").is_file():
        raise SystemExit("Set WRENFIELD_ESTATE to the estate directory holding uri_map.json.")
    return WrenfieldDatasetStore(Path(path))


def build_catalog(
    store: DatasetStore,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    access: AccessPolicy | None = None,
) -> Catalog:
    """Return what the fund serves, as `serve_apps` composes it."""
    return compose(
        policy=policy, research=research, datasets=store, access=access, apps=[build_app()]
    )


def _research(stack: ExitStack) -> ResearchService | None:
    store = get_settings().store
    if is_postgres_url(store) or Path(store).is_file():
        return stack.enter_context(open_research(store, read_only=True)).research
    print("warning: no corpus yet; run `qaos ingest` first, or research tools are left out.")
    return None


def serve() -> None:
    """Serve until interrupted, on ``WRENFIELD_HOST``:``WRENFIELD_PORT``."""
    settings = get_settings()
    access = FileAccessPolicy(settings.access_file) if settings.access_file else None
    with ExitStack() as stack:
        serve_apps(
            [build_app()],
            datasets=estate(),
            research=_research(stack),
            access=access,
            host=os.environ.get("WRENFIELD_HOST", DEFAULT_HOST),
            port=int(os.environ.get("WRENFIELD_PORT", "8000")),
        )


def acceptance(cases: Path) -> int:
    """Run the agreed cases as an operator; return 0 when all pass, 1 when any fails, 2 on error."""
    try:
        gold, digest = load_gold(cases)
    except GoldFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    with ExitStack() as stack:
        catalog = build_catalog(estate(), get_settings().policy(), _research(stack))
        report = run_acceptance(catalog, gold, digest)
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
