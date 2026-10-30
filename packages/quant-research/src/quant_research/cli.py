"""Operator command line for Quant Research: ingest a corpus and query it."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_research.application.retrieval import ResearchQuery
from quant_research.bootstrap import DEFAULT_STORE, open_research

app = typer.Typer(
    help="Quant Research: build and query a Graph RAG knowledge store.", no_args_is_help=True
)
console = Console()

StoreOption = Annotated[
    Path, typer.Option("--store", "-s", help="SQLite knowledge store to create or use.")
]


@app.command()
def ingest(
    corpus: Annotated[Path, typer.Argument(help="A corpus directory or a single file.")],
    store: StoreOption = DEFAULT_STORE,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Ingest Markdown and text files; unchanged files are skipped by content hash."""
    research = open_research(store)
    try:
        result = research.ingest.ingest(corpus)
    except FileNotFoundError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    finally:
        research.close()
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    console.print(
        f"Added {len(result.added)}, unchanged {len(result.unchanged)}, "
        f"skipped {len(result.skipped)}: {result.chunks} chunks, {result.mentions} mentions, "
        f"{result.relations} relations."
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
    as_json: Annotated[bool, typer.Option("--json", help="Print the evidence packet.")] = False,
) -> None:
    """Run `find_related_signals` locally and show the evidence."""
    research = open_research(store)
    try:
        packet = research.research.find_related(ResearchQuery(query=query, k=k, hops=hops))
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


def main() -> None:
    """Run the command-line interface."""
    app()


if __name__ == "__main__":
    main()
