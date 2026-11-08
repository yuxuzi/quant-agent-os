"""From positions to exposures, risk, stress, and limits. Settings come from config.toml."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from .monitor import build_document, open_view, write_report
from .sections import all_checks, available, describe
from .settings import Settings, load_settings

app = typer.Typer(help=__doc__, no_args_is_help=True)
ConfigOption = Annotated[Path, typer.Option(help="The settings file.")]
PositionsOption = Annotated[
    Path | None, typer.Option(help="A positions file, instead of the one in the settings.")
]
AsOfOption = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Use positions and prices up to this date."),
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
    positions: PositionsOption = None,
    as_of: AsOfOption = None,
    out: Annotated[Path, typer.Option(help="Where run folders go.")] = Path("reports"),
    config: ConfigOption = Path("config.toml"),
) -> None:
    """Write the risk report as HTML, Markdown, and JSON in a new run folder."""
    settings = _settings(config)
    try:
        view = open_view(
            settings,
            config.resolve().parent,
            positions=positions,
            as_of=as_of.date() if as_of else None,
        )
        folder = write_report(
            build_document(view),
            view,
            out,
            source=str(positions) if positions else settings.positions,
        )
    except ValueError as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {folder}")
    if settings.positions == "demo" and not positions:
        typer.echo("This is a synthetic demo book. Set positions in config.toml to use yours.")


@app.command()
def check(
    positions: PositionsOption = None,
    as_of: AsOfOption = None,
    config: ConfigOption = Path("config.toml"),
) -> None:
    """Check every limit and firm check. Exits 1 on any breach, so a scheduler can alert."""
    settings = _settings(config)
    try:
        view = open_view(
            settings,
            config.resolve().parent,
            positions=positions,
            as_of=as_of.date() if as_of else None,
        )
        checks = all_checks(view)
    except ValueError as error:
        raise _fail(error) from error
    if not checks:
        raise _fail("No limits are set. Add them under [limits] in config.toml.")
    for item in checks:
        status = "BREACH" if item.breached else "ok"
        typer.echo(
            f"{status}: {item.name}: {item.value:.4f} against {item.limit:.4f} {item.detail}"
        )
    breaches = sum(item.breached for item in checks)
    typer.echo(f"{breaches} of {len(checks)} breached, as of {view.as_of}.")
    if breaches:
        raise typer.Exit(code=1)


@app.command()
def sections() -> None:
    """List the sections you can name in config.toml, including your firm's own."""
    for name, section in sorted(available().items()):
        typer.echo(f"{name}: {describe(section)}")


if __name__ == "__main__":
    app()
