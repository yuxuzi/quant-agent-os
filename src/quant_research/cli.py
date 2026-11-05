"""Quant Research's commands: ingest a corpus and query it. `qaos` mounts them."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quant_agent_os.config import get_settings
from quant_research.application.retrieval import RelatedEntity, ResearchQuery
from quant_research.application.strategy import (
    NoEvidenceError,
    StrategyRequest,
    StrategyService,
    UnknownFocusError,
)
from quant_research.bootstrap import ResearchApp, open_research
from quant_research.config import make_embedder
from quant_research.domain.vocabulary import load_vocabulary
from quant_research.infrastructure.postgres_store import (
    DEFAULT_SCHEMA,
    is_postgres_url,
    redact_url,
)
from quant_research.infrastructure.remote_embedding import EmbeddingServiceError
from quant_research.infrastructure.sqlite_store import StoreError

app = typer.Typer(
    help="Quant Research: build and query a Graph RAG knowledge store.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)  # never insert line breaks into messages

StoreOption = Annotated[
    str | None,
    typer.Option(
        "--store",
        "-s",
        help="SQLite knowledge store file, or a postgresql:// URL (give the password through "
        "PGPASSWORD or ~/.pgpass, not the URL). Default: knowledge.db in the home, or "
        "QUANT_AGENT_OS_STORE.",
    ),
]


SchemaOption = Annotated[
    str,
    typer.Option(
        "--schema",
        envvar="QUANT_AGENT_OS_SCHEMA",
        help="PostgreSQL schema of the store; one per tenant on a shared server (decision 0013).",
    ),
]


def _shown(store: str) -> str:
    """Return the store's location for messages, with any password hidden."""
    return redact_url(store) if is_postgres_url(store) else store


class EmbedderChoice(StrEnum):
    """Embedders the CLI can enable for the vector channel."""

    HASHING = "hashing"
    VOYAGE = "voyage"
    OPENAI = "openai"


EmbedderOption = Annotated[
    EmbedderChoice | None,
    typer.Option(
        "--embedder", help="Enable the vector channel: hashing (offline mock), voyage, or openai."
    ),
]
ModelOption = Annotated[
    str | None,
    typer.Option(
        "--embedding-model", help="Provider model, e.g. voyage-4 or text-embedding-3-large."
    ),
]


def _open(
    store: str | None,
    embedder: EmbedderChoice | None,
    model: str | None,
    *,
    read_only: bool = False,
    schema: str = DEFAULT_SCHEMA,
) -> ResearchApp:
    """Open the store (read-only for search and serving) or exit with an actionable message."""
    try:
        chosen = make_embedder(embedder.value, load_vocabulary(), model=model) if embedder else None
        return open_research(
            store or get_settings().store, embedder=chosen, read_only=read_only, schema=schema
        )
    except (EmbeddingServiceError, StoreError) as error:
        raise _fail(error) from error


def _fail(error: Exception | str) -> typer.Exit:
    errors.print(f"[red]{escape(str(error))}[/red]")
    return typer.Exit(code=1)


@app.command()
def ingest(
    corpus: Annotated[Path, typer.Argument(help="A corpus directory or a single file.")],
    store: StoreOption = None,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    schema: SchemaOption = DEFAULT_SCHEMA,
) -> None:
    """Ingest Markdown, text, and PDF files; unchanged files are skipped by content hash.

    With a hosted --embedder, chunk text is sent to that provider to be embedded.
    """
    research = _open(store, embedder, embedding_model, schema=schema)
    try:
        result = research.ingest.ingest(corpus)
    except (FileNotFoundError, EmbeddingServiceError) as error:
        raise _fail(error) from error
    finally:
        research.close()
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(
        f"Added {len(result.added)}, unchanged {len(result.unchanged)}, "
        f"skipped {len(result.skipped)}: {result.chunks} chunks, {result.mentions} mentions, "
        f"{result.relations} relations, {result.embedded} embeddings."
    )
    location = _shown(store or get_settings().store)
    console.print(f"Corpus snapshot [cyan]{result.snapshot.id}[/cyan] in {location}.")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="A research question.")],
    store: StoreOption = None,
    k: Annotated[int, typer.Option(help="Maximum passages.")] = 6,
    hops: Annotated[int, typer.Option(help="Graph expansion depth (1-2).")] = 1,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the evidence packet.")] = False,
    schema: SchemaOption = DEFAULT_SCHEMA,
) -> None:
    """Run `find_related_signals` locally (read-only) and show the evidence.

    Use the same --embedder and --embedding-model as at ingestion to search by vector.
    """
    research = _open(store, embedder, embedding_model, read_only=True, schema=schema)
    try:
        packet = research.research.find_related(ResearchQuery(query=query, k=k, hops=hops))
    except EmbeddingServiceError as error:
        raise _fail(error) from error
    finally:
        research.close()
    if as_json:
        typer.echo(packet.model_dump_json(indent=2))
        return
    table = Table(title=f"Related to: {query}")
    for column in ("Entity", "Type", "Score", "Connection"):
        table.add_column(column)
    for entity in packet.related:
        table.add_row(entity.name, entity.type, f"{entity.score:.2f}", _describe_path(entity))
    console.print(table)
    for passage in packet.passages:
        console.print(
            f"[cyan]{passage.source.source_id}[/cyan] ({passage.source.location}) "
            f"[dim]{', '.join(passage.channels)}[/dim]"
        )
    for warning in packet.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(f"Corpus snapshot {packet.corpus_snapshot}.")


@app.command()
def strategy(
    focus: Annotated[str, typer.Argument(help="A signal, factor, or strategy, or an entity id.")],
    store: StoreOption = None,
    min_confidence: Annotated[float, typer.Option(help="Weakest relation to include.")] = 0.5,
    as_json: Annotated[bool, typer.Option("--json", help="Print the spec as JSON.")] = False,
) -> None:
    """Run `generate_strategy` locally (read-only) and show the cited strategy spec.

    Save the spec with --json; every item links the passages that state it.
    """
    research = _open(store, None, None, read_only=True)
    try:
        draft = StrategyService(research.store, research.research.matcher).generate(
            StrategyRequest(focus=focus, min_confidence=min_confidence)
        )
    except (UnknownFocusError, NoEvidenceError) as error:
        raise _fail(error) from error
    finally:
        research.close()
    spec = draft.spec
    if as_json:
        typer.echo(spec.model_dump_json(indent=2))
        return
    table = Table(title=f"{spec.name} ({spec.focus.id})")
    for column in ("Section", "Statement", "Sources"):
        table.add_column(column)
    for section in ("signals", "universe", "data", "methods", "metrics", "risks", "context"):
        for item in getattr(spec, section):
            cited = "; ".join(f"{s.source_id} ({s.location})" for s in item.sources)
            table.add_row(section, item.statement, cited)
    console.print(table)
    for question in spec.open_questions:
        console.print(f"[magenta]open:[/magenta] {question}")
    for warning in draft.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(f"Corpus snapshot {spec.corpus_snapshot}.")


def _describe_path(entity: RelatedEntity) -> str:
    """Render the entity's path from its seed, e.g. ``a -uses-> b <-extends- c``."""
    if not entity.path:
        return "mentioned in the best keyword passage"
    nodes = [entity.id]
    for link in reversed(entity.path):
        nodes.append(link.subject if link.object == nodes[-1] else link.object)
    nodes.reverse()
    text = nodes[0]
    for link, after in zip(entity.path, nodes[1:], strict=True):
        arrow = f" -{link.predicate}-> " if link.object == after else f" <-{link.predicate}- "
        text += arrow + after
    return text
