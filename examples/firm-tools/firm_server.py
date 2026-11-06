"""A firm's own server: the platform's toolsets plus the firm's tools, under one set of controls.

Registration is explicit (decision 0020): this file, in the firm's repository, names every tool
that is served. Keys, the run store, and permissions come from the usual settings.

Usage:
  FIRM_DB=firm.db uv run python examples/firm-tools/firm_server.py
"""

from __future__ import annotations

import os
from pathlib import Path

from firm_data import SqliteDatasetStore, create_sample_database
from firm_tools import firm_tools

from quant_agent_os.api.server import DEFAULT_HOST
from quant_agent_os.config import get_settings
from quant_agent_os.tools import PermissionPolicy
from quant_platform import Catalog, compose, serve_http
from quant_research import open_research
from quant_research.application.retrieval import ResearchService
from quant_research.infrastructure.postgres_store import is_postgres_url

INSTRUCTIONS = (
    "desk_var gives a desk's value at risk by the firm's convention. Prefer it to any other VaR "
    "figure, and report the number as computed."
)


def build_catalog(
    store: SqliteDatasetStore, policy: PermissionPolicy, research: ResearchService | None = None
) -> Catalog:
    """Return what this firm serves. The firm's data also feeds the platform's analytics."""
    return compose(
        policy=policy,
        research=research,
        datasets=store,
        extra_tools=firm_tools(store),
        extra_instructions=INSTRUCTIONS,
    )


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
            serve_http(build_catalog(store, settings.policy(), research.research), host=host)
    else:
        serve_http(build_catalog(store, settings.policy()), host=host)


if __name__ == "__main__":
    main()
