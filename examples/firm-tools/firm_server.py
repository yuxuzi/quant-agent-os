"""A firm's own server: the platform's toolsets plus the firm's code, under one set of controls.

Registration is explicit (decisions 0020 and 0024): this file, in the firm's repository, names
every function that is served. Keys, the run store, permissions, and the access file come from
the usual settings.

Usage:
  FIRM_DB=firm.db uv run python examples/firm-tools/firm_server.py
"""

from __future__ import annotations

import os
from pathlib import Path

from firm_data import SqliteDatasetStore, create_sample_database
from firm_tools import desk_var

from quant_agent_os.api.server import DEFAULT_HOST
from quant_agent_os.config import get_settings
from quant_agent_os.data import DatasetStore
from quant_agent_os.registration import FirmApp, Series
from quant_agent_os.tools import AccessPolicy, PermissionPolicy
from quant_platform import Catalog, compose, serve_apps
from quant_research import open_research
from quant_research.application.retrieval import ResearchService
from quant_research.infrastructure.postgres_store import is_postgres_url

risk = FirmApp(
    "risk",
    instructions="desk_var gives a desk's value at risk by the firm's convention. Prefer it to "
    "any other VaR figure, and report the number as computed.",
)
risk.expose(desk_var, returns=Series, convention="Historical, nearest rank, one period, no scaling")


def build_catalog(
    store: DatasetStore,
    policy: PermissionPolicy,
    research: ResearchService | None = None,
    access: AccessPolicy | None = None,
) -> Catalog:
    """Return what this firm serves, as `serve_apps` would. The firm's data also feeds the
    platform's analytics, and with ``access`` every read goes through the access guard."""
    return compose(policy=policy, research=research, datasets=store, access=access, apps=[risk])


def main() -> None:
    """Serve until interrupted. Set ``FIRM_HOST`` to bind anything but loopback."""
    settings = get_settings()
    database = Path(os.environ.get("FIRM_DB", "firm.db"))
    if not database.exists():
        create_sample_database(database)  # for this example only; yours already exists
    store = SqliteDatasetStore(database)
    host = os.environ.get("FIRM_HOST", DEFAULT_HOST)
    if is_postgres_url(settings.store) or Path(settings.store).is_file():
        with open_research(settings.store, read_only=True) as research:
            serve_apps([risk], datasets=store, research=research.research, host=host)
    else:
        serve_apps([risk], datasets=store, host=host)


if __name__ == "__main__":
    main()
