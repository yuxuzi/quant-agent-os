"""From returns to your tear sheets. Settings come from config.toml in the current directory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer
from quant_toolbox.data import TableError
from quant_toolbox.report import Document, render_markdown

from .reconcile import as_block, reconcile
from .reporting import (
    build_context,
    build_document,
    load_returns,
    open_returns,
    write_reconciliation,
    write_report,
)
from .sections import available, describe
from .settings import Settings, load_settings

app = typer.Typer(help=__doc__, no_args_is_help=True)
ConfigOption = Annotated[Path, typer.Option(help="The settings file.")]
ReturnsOption = Annotated[
    Path | None, typer.Option(help="A returns file, instead of the one in the settings.")
]


def _settings(config: Path) -> Settings:
    try:
        return load_settings(config)
    except ValueError as error:
        raise _fail(error) from error


def _fail(error: Exception | str) -> typer.Exit:
    typer.secho(str(error), fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@app.command()
def report(
    tearsheet: Annotated[
        str | None, typer.Option(help="A tear sheet in the settings (default: the settings').")
    ] = None,
    returns: ReturnsOption = None,
    as_of: Annotated[
        dt.datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Report as of this date; later rows are ignored."),
    ] = None,
    out: Annotated[Path, typer.Option(help="Where run folders go.")] = Path("reports"),
    config: ConfigOption = Path("config.toml"),
) -> None:
    """Write a tear sheet as HTML, Markdown, Excel, and JSON in a new run folder."""
    settings = _settings(config)
    try:
        table = (
            load_returns(returns, settings)
            if returns
            else open_returns(settings, config.resolve().parent)
        )
        context = build_context(table, settings, as_of.date() if as_of else None)
        document = build_document(context, tearsheet)
        source = str(returns) if returns else settings.returns
        folder = write_report(
            document,
            context,
            out,
            label=tearsheet or settings.default_tearsheet,
            source=source,
        )
    except ValueError as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {folder}")
    if settings.returns == "demo" and not returns:
        typer.echo("These are synthetic demo returns. Set returns in config.toml to use yours.")


@app.command("reconcile")
def reconcile_command(
    reference: Annotated[
        Path | None,
        typer.Argument(
            help="Reference returns, such as your administrator's (default: [reference] file)."
        ),
    ] = None,
    tolerance_bps: Annotated[
        float, typer.Option(help="Largest difference to accept per month, in basis points.")
    ] = 1.0,
    returns: ReturnsOption = None,
    out: Annotated[Path, typer.Option(help="Where run folders go.")] = Path("reports"),
    config: ConfigOption = Path("config.toml"),
) -> None:
    """Compare monthly returns with reference figures. Exits 1 if anything fails to agree.

    Each run writes its result to reconcile.json in a new run folder under ``out``.
    """
    settings = _settings(config)
    try:
        ours = (
            load_returns(returns, settings)
            if returns
            else open_returns(settings, config.resolve().parent)
        )
        if reference is None:
            if not settings.reference.file:
                raise TableError("Name a reference file, or set file under [reference].")
            reference = config.resolve().parent / settings.reference.file
        theirs = load_returns(reference, settings, reference=True)
        benchmarks = settings.benchmark_columns()
        books = [name for name in ours.columns if name not in benchmarks]
        result = reconcile(ours.select(books), theirs, tolerance_bps)
        source = str(returns) if returns else settings.returns
        folder = write_reconciliation(
            result, ours.select(books), reference, settings, out, source=source
        )
    except (ValueError, OSError) as error:
        raise _fail(error) from error
    typer.echo(render_markdown(Document("Reconciliation", blocks=[as_block(result)])))
    for note in result.only_ours:
        typer.echo(f"Ours only: {note}")
    for note in result.only_reference:
        typer.echo(f"Reference only: {note}")
    typer.echo(f"Wrote {folder}")
    if not result.clean:
        typer.secho(
            f"{len(result.breaks)} breaks; {len(result.only_reference)} reference items unmatched.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Clean: {result.summary}")


@app.command()
def sections() -> None:
    """List the sections you can name in a tear sheet, including your firm's own."""
    for name, section in sorted(available().items()):
        typer.echo(f"{name}: {describe(section)}")


@app.command()
def tearsheets(config: ConfigOption = Path("config.toml")) -> None:
    """List the tear sheets in the settings, with their sections."""
    for name, sheet in sorted(_settings(config).tearsheets.items()):
        typer.echo(f"{name}: {', '.join(sheet.sections)}")


if __name__ == "__main__":
    app()
