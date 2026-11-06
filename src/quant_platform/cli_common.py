"""What the platform's commands share: output, default paths, options, and opening a catalog."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape

from quant_agent_os.config import get_settings
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.tools import AccessPolicy
from quant_platform.composition import (
    DEFAULT_TOOLSETS,
    TOOLSET_NEEDS,
    TOOLSETS,
    Catalog,
    Toolset,
    available_toolsets,
    compose,
)
from quant_research import ResearchApp, open_research
from quant_research.cli import EmbedderChoice
from quant_research.config import make_embedder
from quant_research.infrastructure.postgres_store import is_postgres_url
from quant_research.infrastructure.remote_embedding import EmbeddingServiceError
from quant_research.infrastructure.store_common import StoreError

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
    for name, (_, hint) in TOOLSET_NEEDS.items():
        if name in chosen and name not in available_toolsets():
            errors.print(f"[red]error:[/red] the {name} toolset is not installed; {escape(hint)}.")
            raise typer.Exit(2)
    return [item for item in TOOLSETS if item in chosen]


def _factory() -> AppFactory:
    from quant_app_factory.bootstrap import open_factory
    from quant_app_factory.domain.ports import ScaffoldError
    from quant_app_factory.infrastructure.settings_file import SettingsError

    try:
        return open_factory()
    except (ScaffoldError, SettingsError) as error:
        errors.print(f"[red]error:[/red] {escape(str(error))}")
        raise typer.Exit(1) from error


def open_catalog(
    toolsets: list[Toolset],
    store: str | None,
    datasets: Path | None,
    *,
    chosen: bool = True,
    embedder: EmbedderChoice | None = None,
    embedding_model: str | None = None,
    access: AccessPolicy | None = None,
) -> tuple[Catalog, ResearchApp | None]:
    """Open what the toolsets need and compose them, or exit with an actionable message.

    Without a store, the research toolset uses the configured one if it exists. Otherwise it is
    an error when the toolsets were ``chosen``, and skipped with a warning when they are the
    default, so a client configured once (a plugin) still starts.
    """
    research: ResearchApp | None = None
    settings = get_settings()
    for name, (_, hint) in TOOLSET_NEEDS.items():
        if name in toolsets and name not in available_toolsets():
            errors.print(
                f"[yellow]warning:[/yellow] the {name} toolset is not served; {escape(hint)}."
            )
            toolsets = [item for item in toolsets if item != name]
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
                "research toolset is not served. Ingest with `qaos ingest DIR`, or try `qaos "
                "init --sample`."
            )
            toolsets = [item for item in toolsets if item != "research"]
    if "research" in toolsets and store is not None:
        try:
            chosen_embedder = (
                make_embedder(embedder.value, model=embedding_model) if embedder else None
            )
            research = open_research(store, embedder=chosen_embedder, read_only=True)
        except (EmbeddingServiceError, StoreError) as error:
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
        access=access,
    )
    return catalog, research
