"""What the platform's commands share: output, default paths, options, and opening a catalog."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape

from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_platform.composition import (
    DEFAULT_TOOLSETS,
    FACTORY_EXTRA,
    TOOLSETS,
    Catalog,
    Toolset,
    available_toolsets,
    compose,
)
from quant_research import ResearchApp, open_research
from quant_research.infrastructure.postgres_store import is_postgres_url
from quant_research.infrastructure.sqlite_store import StoreError

if TYPE_CHECKING:
    from quant_app_factory import AppFactory

console = Console()
errors = Console(stderr=True, soft_wrap=True)

ToolsetsOption = Annotated[
    str | None,
    typer.Option(
        help=f"Comma-separated toolsets to serve: {', '.join(TOOLSETS)} (default: "
        f"{', '.join(DEFAULT_TOOLSETS)})."
    ),
]
StoreOption = Annotated[
    str | None,
    typer.Option(
        help="Research knowledge store: a SQLite path or postgresql:// URL (default: "
        "QUANT_AGENT_OS_STORE, or knowledge.db in the home, when it exists).",
    ),
]
DatasetsOption = Annotated[
    Path | None,
    typer.Option(help="Datasets directory (default: QUANT_AGENT_OS_DATASETS_DIR)."),
]


def parse_toolsets(value: str | None) -> list[Toolset]:
    if value is None:
        return list(DEFAULT_TOOLSETS)
    chosen = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(chosen) - set(TOOLSETS))
    if unknown or not chosen:
        errors.print(f"[red]error:[/red] choose toolsets from {', '.join(TOOLSETS)}.")
        raise typer.Exit(2)
    if "factory" in chosen and "factory" not in available_toolsets():
        errors.print(
            f"[red]error:[/red] the factory toolset is not installed; {escape(FACTORY_EXTRA)}."
        )
        raise typer.Exit(2)
    return [item for item in TOOLSETS if item in chosen]


def _factory() -> AppFactory:
    from quant_app_factory.cli import default_factory

    return default_factory()


def open_catalog(
    toolsets: list[Toolset], store: str | None, datasets: Path | None, *, chosen: bool = True
) -> tuple[Catalog, ResearchApp | None]:
    """Open what the toolsets need and compose them, or exit with an actionable message.

    Without a store, the research toolset uses the configured one if it exists. Otherwise it is
    an error when the toolsets were ``chosen``, and skipped with a warning when they are the
    default, so a client configured once (a plugin) still starts.
    """
    research: ResearchApp | None = None
    settings = get_settings()
    if "research" in toolsets and store is None:
        if is_postgres_url(settings.store) or Path(settings.store).is_file():
            store = settings.store
        elif chosen:
            errors.print(
                f"[red]error:[/red] no knowledge store at {settings.store}; ingest first, or "
                "give --store."
            )
            raise typer.Exit(2)
        else:
            errors.print(
                f"[yellow]warning:[/yellow] no knowledge store at {settings.store}, so the "
                "research toolset is not served. Ingest with `quant-research ingest DIR`."
            )
            toolsets = [item for item in toolsets if item != "research"]
    if "research" in toolsets and store is not None:
        try:
            research = open_research(store, read_only=True)
        except StoreError as error:
            errors.print(f"[red]error:[/red] {error}")
            raise typer.Exit(1) from error
    catalog = compose(
        policy=settings.policy(),
        research=research.research if research else None,
        datasets=(
            DirectoryDatasetStore(datasets or settings.datasets_dir)
            if "analytics" in toolsets
            else None
        ),
        factory=_factory() if "factory" in toolsets else None,
    )
    return catalog, research
