"""`open_platform`: from a notebook to the home's store, datasets, and audit in one call."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from quant_agent_os.config import Settings, get_settings
from quant_agent_os.data import DirectoryDatasetStore, MemoryDatasetStore
from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import PermissionPolicy, ToolSpec
from quant_platform.composition import TOOLSET_NEEDS, available_toolsets, compose
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.postgres_store import is_postgres_url


class NotebookPlatform(Platform):
    """A `Platform` over the home, with ``data`` for pandas objects, from `open_platform`."""

    def __init__(
        self,
        tools: list[ToolSpec[Any, Any]],
        *,
        data: MemoryDatasetStore,
        research: ResearchApp | None,
        policy: PermissionPolicy,
        run_store: SqliteRunStore,
    ) -> None:
        super().__init__(tools, policy=policy, run_store=run_store)
        self.data = data
        self._research = research

    def close(self) -> None:
        """Close the run store and the knowledge store."""
        super().close()
        if self._research is not None:
            self._research.close()


def open_platform() -> NotebookPlatform:
    """Open the home the `qaos` commands use, and return a ready `Platform`.

    The tools are the ones `qaos serve` serves by default, under the same permissions from the
    settings, and every call is recorded where `qaos runs` reads it. The knowledge store opens
    read-only. ``platform.data.put("fund", df)`` adds a pandas object for this session, on top of
    the home's datasets, and writes nothing to the home:

        with open_platform() as platform:
            platform.data.put("fund", returns)
            series = {"dataset": "fund", "column": "fund"}
            metrics = platform.call("analyze_performance", returns=series).result()["metrics"]

    What the home or this installation lacks is a warning, and its tools are not there: a store
    that does not exist yet is no reason to refuse analytics.
    """
    settings = get_settings()
    data = MemoryDatasetStore(DirectoryDatasetStore(settings.datasets_dir))
    research = _open_research(settings)
    try:
        analytics = "analytics" in available_toolsets()
        if not analytics:
            warnings.warn(
                f"Analytics tools are not available; {TOOLSET_NEEDS['analytics'][1]}.",
                stacklevel=2,
            )
        policy = settings.policy()
        catalog = compose(
            policy=policy,
            research=research.research if research else None,
            datasets=data if analytics else None,
        )
        return NotebookPlatform(
            catalog.tools,
            data=data,
            research=research,
            policy=policy,
            run_store=SqliteRunStore(settings.runs_file),
        )
    except Exception:
        if research is not None:
            research.close()
        raise


def _open_research(settings: Settings) -> ResearchApp | None:
    if is_postgres_url(settings.store) or Path(settings.store).is_file():
        return open_research(settings.store, read_only=True)
    warnings.warn(
        f"No knowledge store at {settings.store}, so research tools are not available. Ingest "
        "with `qaos ingest DIR`, or try `qaos init --sample`.",
        stacklevel=3,
    )
    return None
