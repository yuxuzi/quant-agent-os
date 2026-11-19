"""Corvane Equity Partners' own server: the platform's toolsets plus the fund's benchmark-relative
tools, over the fund's Snowflake and S3 data, under its sign-in and entitlements (decisions 0020,
0024, and 0028). It runs as one container on ECS Fargate behind an ALB (deploy/).

Registration is explicit: this file names every tool the fund serves. Settings (keys, sign-in
file, run store, access file) come from the usual QUANT_AGENT_OS_* variables; the fund's own are
``CORVANE_SQL_URL`` and, for Snowflake, ``CORVANE_SNOWFLAKE_USER`` and
``CORVANE_SNOWFLAKE_PRIVATE_KEY``, both injected from Secrets Manager.

Usage:
  CORVANE_SQL_URL=estate://<dir> python corvane_server.py                  # serve
  CORVANE_SQL_URL=estate://<dir> python corvane_server.py acceptance corvane_acceptance.toml
"""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path

SITE = Path(__file__).resolve().parent
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

import corvane_tools as tools  # noqa: E402
from corvane_store import CorvaneDatasetStore, connect  # noqa: E402

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

WIDE_OUTPUT = 400_000
INSTRUCTIONS = (
    "Corvane's tools answer benchmark-relative questions for its long-only equity accounts. "
    "Pass weights, returns and reference data as dataset references from list_datasets "
    "(weights_<account>, bench_w_<benchmark>, px_ret_usd, ref_security, ...). Prefer these tools "
    "to computing active share, tracking error, or attribution any other way, and report the "
    "numbers as computed. Sector codes are ref_security.sector_code."
)


def build_app() -> FirmApp:
    """Return Corvane's app: every fund-specific tool, with its convention stated."""
    app = FirmApp("corvane", instructions=INSTRUCTIONS, prefix=True)
    app.expose(
        tools.active_share,
        portfolio=Frame,
        benchmark=Frame,
        convention="0.5 * sum |wp - wb| over the union of names, end of day, cash included",
    )
    app.expose(
        tools.tracking_error,
        portfolio_returns=Series,
        benchmark_returns=Series,
        convention="Sample std (n-1) of daily active return, times sqrt(252)",
    )
    app.expose(
        tools.tracking_error_ex_ante,
        portfolio=Frame,
        benchmark=Frame,
        returns=Frame,
        convention="Historical simulation of today's active weights over a trailing window",
    )
    app.expose(
        tools.attribution_single,
        portfolio=Frame,
        benchmark=Frame,
        returns=Frame,
        sector_code=Series,
        convention="Brinson-Fachler by sector, buy and hold from the start day's weights",
    )
    app.expose(
        tools.attribution_linked,
        portfolio=Frame,
        benchmark=Frame,
        returns=Frame,
        sector_code=Series,
        convention="Brinson-Fachler per period, linked by Carino to the compounded excess return",
    )
    app.expose(
        tools.currency_effect,
        portfolio=Frame,
        benchmark=Frame,
        returns_usd=Frame,
        returns_local=Frame,
        fx_returns=Frame,
        currency_code=Series,
        convention="USD active return = local + currency allocation + residual, buy and hold",
        max_output_bytes=WIDE_OUTPUT,  # the evidence names every column of every frame it read
    )
    app.expose(
        tools.days_to_liquidate,
        portfolio=Frame,
        adv_usd=Frame,
        convention="Value / (participation * 20-row ADV in USD), cash excluded",
    )
    app.expose(
        tools.mandate_check,
        portfolio=Frame,
        excluded=Series,
        convention="Positive end-of-day weight in an excluded name, or above a position cap",
    )
    return app


def build_catalog(
    store: DatasetStore,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    access: AccessPolicy | None = None,
) -> Catalog:
    """Return what Corvane serves, as `serve_apps` composes it."""
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
    """Serve until interrupted, on ``CORVANE_HOST``:``CORVANE_PORT`` (loopback, 8000 by default)."""
    settings = get_settings()
    access = FileAccessPolicy(settings.access_file) if settings.access_file else None
    with ExitStack() as stack:
        serve_apps(
            [build_app()],
            datasets=CorvaneDatasetStore(connect()),
            research=_research(stack),
            access=access,
            host=os.environ.get("CORVANE_HOST", DEFAULT_HOST),
            port=int(os.environ.get("CORVANE_PORT", "8000")),
        )


def acceptance(cases: Path) -> int:
    """Run the agreed cases as the operator; return 0 if all pass, 1 if any fails, 2 if unread."""
    try:
        gold, digest = load_gold(cases)
    except GoldFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    with ExitStack() as stack:
        catalog = build_catalog(
            CorvaneDatasetStore(connect()), get_settings().policy(), _research(stack)
        )
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
