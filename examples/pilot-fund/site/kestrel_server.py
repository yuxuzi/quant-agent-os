"""Kestrel Point Capital's own server: the platform's toolsets, the fund's warehouse, and its legacy
code as tools, under the fund's sign-in and entitlements (decisions 0020, 0024, and 0028).

Registration is explicit: this file and legacy_tools/server.py name every tool the fund serves.
Keys, sign-in, the run store, permissions, and the access file come from the usual settings
(`kestrel.env` lists the fund's). The warehouse is read where it is, through
`WarehouseDatasetStore`, and the corpus is the one `qaos ingest` built in the home.

Usage:
  KPC_WAREHOUSE=warehouse/kestrel.db python kestrel_server.py                # serve
  KPC_WAREHOUSE=warehouse/kestrel.db python kestrel_server.py acceptance acceptance.toml
"""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path

SITE = Path(__file__).resolve().parent
FACILITY = Path(os.environ.get("KPC_FACILITY", SITE.parent / "facility"))
# The draft imports its modules by bare name, and kestrel_quant is not installed as a package:
# both are put on the path here, as the fund's own deployment would install them.
for _path in (SITE / "legacy_tools", SITE, FACILITY):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import facades  # noqa: E402
import server as legacy  # noqa: E402
from warehouse_store import WarehouseDatasetStore  # noqa: E402

from quant_agent_os.api.server import DEFAULT_HOST  # noqa: E402
from quant_agent_os.config import get_settings  # noqa: E402
from quant_agent_os.data import DatasetStore  # noqa: E402
from quant_agent_os.registration import FirmApp  # noqa: E402
from quant_agent_os.tools import AccessPolicy, FileAccessPolicy, PermissionPolicy  # noqa: E402
from quant_platform import Catalog, compose, serve_apps  # noqa: E402
from quant_platform.acceptance import GoldFileError, load_gold, run_acceptance  # noqa: E402
from quant_research import open_research  # noqa: E402
from quant_research.application.retrieval import ResearchService  # noqa: E402
from quant_research.infrastructure.postgres_store import is_postgres_url  # noqa: E402


def warehouse() -> WarehouseDatasetStore:
    """Return the fund's warehouse, named by ``KPC_WAREHOUSE`` as the legacy code reads it."""
    path = os.environ.get("KPC_WAREHOUSE")
    if not path or not Path(path).is_file():
        raise SystemExit(
            "Set KPC_WAREHOUSE to the warehouse file, such as warehouse/kestrel.db; it is "
            "missing or unset."
        )
    return WarehouseDatasetStore(Path(path))


def build_apps(access: AccessPolicy | None, service_url: str | None = None) -> list[FirmApp]:
    """Return the fund's apps, with the desk P&L facade scoped by ``access``; the risk service
    is at ``service_url``, or ``KPC_RISK_SERVICE_URL``."""
    facades.use_access(access)
    return legacy.build_apps(service_url or legacy.SERVICE_URL)


def build_catalog(
    store: DatasetStore,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    access: AccessPolicy | None = None,
    service_url: str | None = None,
) -> Catalog:
    """Return what the fund serves, as `serve_apps` composes it."""
    return compose(
        policy=policy,
        research=research,
        datasets=store,
        access=access,
        apps=build_apps(access, service_url),
    )


def _research(stack: ExitStack) -> ResearchService | None:
    store = get_settings().store
    if is_postgres_url(store) or Path(store).is_file():
        return stack.enter_context(open_research(store, read_only=True)).research
    print("warning: no corpus yet; run `qaos ingest` first, or research tools are left out.")
    return None


def serve() -> None:
    """Serve until interrupted, on ``KPC_HOST``:``KPC_PORT`` (loopback, 8000 by default)."""
    settings = get_settings()
    access = FileAccessPolicy(settings.access_file) if settings.access_file else None
    with ExitStack() as stack:
        serve_apps(
            build_apps(access),
            datasets=warehouse(),
            research=_research(stack),
            access=access,
            host=os.environ.get("KPC_HOST", DEFAULT_HOST),
            port=int(os.environ.get("KPC_PORT", "8000")),
        )


def acceptance(cases: Path) -> int:
    """Run the agreed cases against the fund's tools, as an operator; return the exit code.

    Like `qaos eval`, it runs as no caller, so no access file applies: it checks the numbers,
    not who may see them. Exits 0 when every case passes, 1 when any fails, 2 when the cases
    cannot be read.
    """
    try:
        gold, digest = load_gold(cases)
    except GoldFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    with ExitStack() as stack:
        catalog = build_catalog(warehouse(), get_settings().policy(), _research(stack))
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
