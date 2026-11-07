"""From signals or target weights to net returns, and a returns file for the report."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from .backtest import run_with_tables
from .data import Panel, open_panel
from .parameters import ParameterError, Parameters, load_parameters
from .report import equity_svg, render_comparison, render_markdown
from .results import latest_run, load_results, save_run
from .tables import Table, save_table
from .walk_forward import run_walk_forward

app = typer.Typer(help=__doc__, no_args_is_help=True)
ConfigOption = Annotated[Path | None, typer.Option(help="The settings (default: config.toml).")]
PricesOption = Annotated[Path | None, typer.Option(help="A price file, instead of the settings'.")]
SignalOption = Annotated[str | None, typer.Option(help="A signal name, instead of the settings'.")]
SignalFile = Annotated[
    Path | None, typer.Option(help="A file of scores, such as alpha-research's.")
]
WeightsFile = Annotated[Path | None, typer.Option(help="A file of target weights.")]
RESULTS = Path("results")


def _settings(config: Path | None, panel_source: Path | None) -> tuple[Parameters, Path]:
    """Read the parameters; their relative paths start at the config file's folder."""
    path = config or Path("config.toml")
    parameters = load_parameters(path) if path.is_file() else Parameters()
    base = path.resolve().parent
    if panel_source is not None:
        parameters = parameters.updated(data=str(panel_source.resolve()))
    return parameters, base


def _absolute(value: str | None, base: Path) -> str | None:
    return None if value is None else str(base / value)


def _fail(error: Exception | str) -> typer.Exit:
    typer.secho(str(error), fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


def _chosen(
    config: Path | None,
    prices: Path | None,
    signal: str | None,
    signal_file: Path | None,
    weights_file: Path | None,
) -> tuple[Parameters, Path]:
    parameters, base = _settings(config, prices)
    if signal_file is not None or weights_file is not None or signal is not None:
        parameters = replace(
            parameters,
            signal=signal or parameters.signal,
            signal_file=str(signal_file.resolve()) if signal_file else None,
            weights_file=str(weights_file.resolve()) if weights_file else None,
        )
    else:
        parameters = replace(
            parameters,
            signal_file=_absolute(parameters.signal_file, base),
            weights_file=_absolute(parameters.weights_file, base),
        )
    return parameters, base


def _save_tables(folder: Path, tables: dict[str, Table], panel: Panel, what: str) -> None:
    for name, table in tables.items():
        save_table(
            folder / name,
            table,
            made_by=f"strategy-backtest {what}",
            inputs={"prices": f"sha256:{panel.fingerprint()}"},
        )


@app.command()
def backtest(
    signal: SignalOption = None,
    signal_file: SignalFile = None,
    weights_file: WeightsFile = None,
    prices: PricesOption = None,
    label: Annotated[str | None, typer.Option(help="A name for this run's folder.")] = None,
    config: ConfigOption = None,
) -> None:
    """Backtest with costs. Writes returns.csv for the performance-report application."""
    try:
        parameters, base = _chosen(config, prices, signal, signal_file, weights_file)
        panel = open_panel(parameters.data or "demo", base)
        results, tables = run_with_tables(panel, parameters)
        folder = save_run(
            RESULTS, results, parameters=parameters, fingerprint=panel.fingerprint(), label=label
        )
        _save_tables(folder, tables, panel, results["title"])
    except (ValueError, ParameterError) as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {folder}")
    if (parameters.data or "demo") == "demo":
        typer.echo("These are synthetic demo prices: a random walk, where no signal has an edge.")
    metrics = results["metrics"]
    typer.echo(
        f"Net Sharpe {metrics['sharpe_ratio']:.2f} (in-sample), annual turnover "
        f"{metrics['annual_turnover']:.1f}"
    )


@app.command("walk-forward")
def walk_forward(
    signal: SignalOption = None,
    prices: PricesOption = None,
    label: Annotated[str | None, typer.Option(help="A name for this run's folder.")] = None,
    config: ConfigOption = None,
) -> None:
    """Choose the lookback on training data, and report only out of sample."""
    try:
        parameters, base = _chosen(config, prices, signal, None, None)
        panel = open_panel(parameters.data or "demo", base)
        results = run_walk_forward(panel, parameters)
        folder = save_run(
            RESULTS, results, parameters=parameters, fingerprint=panel.fingerprint(), label=label
        )
    except (ValueError, ParameterError) as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {folder}")
    typer.echo(f"Out-of-sample net Sharpe {results['metrics']['sharpe_ratio']:.2f}")


@app.command()
def report(
    runs: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Run folders: one to report, several to compare (default: the latest)."
        ),
    ] = None,
    out: Annotated[Path | None, typer.Option(help="The Markdown file to write.")] = None,
) -> None:
    """Render one run, or compare several, as Markdown with an equity chart."""
    try:
        folders = runs or [latest_run(RESULTS)]
        loaded = [(folder.name, load_results(folder)) for folder in folders]
        path = out or (folders[0] / "report.md" if len(folders) == 1 else RESULTS / "comparison.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        svg = equity_svg(loaded)
        chart = None
        if svg:
            chart = path.with_suffix(".svg")
            chart.write_text(svg, "utf-8")
        name = chart.name if chart else None
        text = (
            render_markdown(loaded[0][1], name)
            if len(loaded) == 1
            else render_comparison(loaded, name)
        )
        path.write_text(text, "utf-8")
    except ValueError as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {path}")


if __name__ == "__main__":
    app()
