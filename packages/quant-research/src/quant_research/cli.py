"""Operator command line for Quant Research: ingest a corpus and query it."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_agent_os.config import get_settings
from quant_agent_os.mcp import MCPUnavailableError, run_stdio
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import PermissionPolicy, ToolRegistry
from quant_research.application.retrieval import ResearchQuery
from quant_research.bootstrap import DEFAULT_STORE, ResearchApp, open_research
from quant_research.config import make_embedder
from quant_research.domain.vocabulary import load_vocabulary
from quant_research.infrastructure.remote_embedding import EmbeddingServiceError
from quant_research.tools import research_tools

app = typer.Typer(
    help="Quant Research: build and query a Graph RAG knowledge store.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True)

MCP_INSTRUCTIONS = (
    "Quant Research searches the user's own ingested research corpus (notes, papers, PDFs). "
    "Use find_related_signals for questions about factors, signals, strategies, and methods; "
    "cite every claim with the passage's source and location; call get_source_passage for the "
    "full text of a passage. Results are retrieved evidence, not investment advice."
)

StoreOption = Annotated[
    Path, typer.Option("--store", "-s", help="SQLite knowledge store to create or use.")
]


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


def _open(store: Path, embedder: EmbedderChoice | None, model: str | None) -> ResearchApp:
    try:
        chosen = make_embedder(embedder.value, load_vocabulary(), model=model) if embedder else None
    except EmbeddingServiceError as error:
        raise _fail(error) from error
    return open_research(store, embedder=chosen)


def _fail(error: Exception) -> typer.Exit:
    errors.print(f"[red]{error}[/red]")
    return typer.Exit(code=1)


@app.command()
def ingest(
    corpus: Annotated[Path, typer.Argument(help="A corpus directory or a single file.")],
    store: StoreOption = DEFAULT_STORE,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Ingest Markdown, text, and PDF files; unchanged files are skipped by content hash.

    With a hosted --embedder, chunk text is sent to that provider to be embedded.
    """
    research = _open(store, embedder, embedding_model)
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
    console.print(f"Corpus snapshot [cyan]{result.snapshot.id}[/cyan] in {store}.")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="A research question.")],
    store: StoreOption = DEFAULT_STORE,
    k: Annotated[int, typer.Option(help="Maximum passages.")] = 6,
    hops: Annotated[int, typer.Option(help="Graph expansion depth (1-2).")] = 1,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the evidence packet.")] = False,
) -> None:
    """Run `find_related_signals` locally and show the evidence.

    Use the same --embedder and --embedding-model as at ingestion to search by vector.
    """
    research = _open(store, embedder, embedding_model)
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
    for column in ("Entity", "Type", "Score", "Strongest link"):
        table.add_column(column)
    for entity in packet.related:
        strongest = (
            f"{entity.links[0].subject} {entity.links[0].predicate} {entity.links[0].object}"
            if entity.links
            else "mentioned in the best keyword passage"
        )
        table.add_row(entity.name, entity.type, f"{entity.score:.2f}", strongest)
    console.print(table)
    for passage in packet.passages:
        console.print(
            f"[cyan]{passage.source.source_id}[/cyan] ({passage.source.location}) "
            f"[dim]{', '.join(passage.channels)}[/dim]"
        )
    for warning in packet.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(f"Corpus snapshot {packet.corpus_snapshot}.")


@app.command("serve-mcp")
def serve_mcp(
    store: StoreOption = DEFAULT_STORE,
    embedder: EmbedderOption = None,
    embedding_model: ModelOption = None,
) -> None:
    """Serve the read-only research tools to an MCP client (Claude, Cursor, ...) over stdio.

    Ingest first; the server never writes to the store. Permissions come from
    QUANT_AGENT_OS_GRANTED_PERMISSIONS (read-only by default). Messages go to stderr because
    stdout carries the protocol.
    """
    if not store.exists():
        raise _fail(FileNotFoundError(f"No knowledge store at {store}; run quant-research ingest."))
    research = _open(store, embedder, embedding_model)
    try:
        policy = PermissionPolicy(granted=get_settings().granted_permissions)
        tools = research_tools(research.research)
        for spec in tools:
            missing = sorted(p.value for p in spec.permissions - policy.granted)
            if missing:
                errors.print(
                    f"[yellow]warning:[/yellow] {spec.name} needs {', '.join(missing)}, which is "
                    "not granted, so calls will be denied. Grant it with "
                    "QUANT_AGENT_OS_GRANTED_PERMISSIONS, or serve without a hosted embedder."
                )
        run_stdio(
            ToolExecutor(ToolRegistry(tools), policy),
            name="quant-research",
            instructions=MCP_INSTRUCTIONS,
        )
    except MCPUnavailableError as error:
        raise _fail(error) from error
    finally:
        research.close()


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
