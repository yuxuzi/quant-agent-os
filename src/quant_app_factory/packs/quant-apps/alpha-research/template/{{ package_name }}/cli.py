"""From prices to signal evidence, and a signal file for the next stage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from .data import open_panel
from .firm.signals import SIGNALS as FIRM_SIGNALS
from .parameters import ParameterError, Parameters, load_parameters
from .report import render_markdown
from .results import latest_run, load_results, save_run
from .signals import PARAMETERS, look_ahead_bars
from .study import run_study, signal_for
from .tables import Table, save_table

app = typer.Typer(help=__doc__, no_args_is_help=True)
ConfigOption = Annotated[Path | None, typer.Option(help="The settings (default: config.toml).")]
PricesOption = Annotated[Path | None, typer.Option(help="A price file, instead of the settings'.")]
SignalOption = Annotated[str | None, typer.Option(help="A signal name, instead of the settings'.")]
FileOption = Annotated[Path | None, typer.Option(help="A file of scores from elsewhere.")]
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
    config: Path | None, prices: Path | None, signal: str | None, signal_file: Path | None
) -> tuple[Parameters, Path]:
    parameters, base = _settings(config, prices)
    if signal_file is not None:
        return parameters.updated(signal_file=str(signal_file.resolve())), base
    if signal is not None:
        return replace(parameters, signal=signal, signal_file=None), base
    return parameters.updated(signal_file=_absolute(parameters.signal_file, base)), base


@app.command()
def study(
    signal: SignalOption = None,
    signal_file: FileOption = None,
    prices: PricesOption = None,
    label: Annotated[str | None, typer.Option(help="A name for this run's folder.")] = None,
    config: ConfigOption = None,
) -> None:
    """Evaluate a signal: information coefficients, decay, and quantile spreads."""
    try:
        parameters, base = _chosen(config, prices, signal, signal_file)
        panel = open_panel(parameters.data or "demo", base)
        results = run_study(panel, parameters)
        folder = save_run(
            RESULTS, results, parameters=parameters, fingerprint=panel.fingerprint(), label=label
        )
    except (ValueError, ParameterError) as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {folder}")
    if (parameters.data or "demo") == "demo":
        typer.echo("These are synthetic demo prices: a random walk, where no signal has an edge.")
    metrics = results["metrics"]
    typer.echo(
        f"Mean rank IC {metrics['mean_rank_ic']:.4f}, t-stat {metrics['rank_ic_t_stat']:.2f}"
    )


@app.command()
def export(
    out: Annotated[Path, typer.Argument(help="The CSV file to write, for strategy-backtest.")],
    signal: SignalOption = None,
    prices: PricesOption = None,
    config: ConfigOption = None,
) -> None:
    """Write a signal's scores as a file the strategy-backtest application reads."""
    try:
        parameters, base = _chosen(config, prices, signal, None)
        panel = open_panel(parameters.data or "demo", base)
        scores, chosen, name = signal_for(panel, parameters)
        table = Table(panel.dates, panel.assets, np.asarray(scores, dtype=np.float64))
        save_table(
            out,
            table,
            made_by=f"alpha-research {name} {chosen}",
            inputs={"prices": f"sha256:{panel.fingerprint()}"},
        )
    except (ValueError, ParameterError) as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {out}")


@app.command("check-signal")
def check_signal(
    name: Annotated[str, typer.Argument(help="A signal name.")],
    prices: PricesOption = None,
    config: ConfigOption = None,
) -> None:
    """Test a signal for look-ahead: its scores must not change when later prices go away."""
    try:
        parameters, base = _settings(config, prices)
        panel = open_panel(parameters.data or "demo", base)
        bad = look_ahead_bars(name, panel.prices)
    except (ValueError, ParameterError) as error:
        raise _fail(error) from error
    if bad:
        raise _fail(f"{name} looks ahead: its values change at bar {bad[0]}.")
    typer.echo(f"{name}: no look-ahead found in prices.")


@app.command()
def signals() -> None:
    """List the signals you can name, including your firm's own."""
    for name in sorted({*PARAMETERS, *FIRM_SIGNALS}):
        typer.echo(name)


@app.command()
def report(
    run: Annotated[Path | None, typer.Argument(help="A run folder (default: the latest).")] = None,
) -> None:
    """Render a run as Markdown, in its folder."""
    try:
        folder = run or latest_run(RESULTS)
        path = folder / "report.md"
        path.write_text(render_markdown(load_results(folder)), "utf-8")
    except ValueError as error:
        raise _fail(error) from error
    typer.echo(f"Wrote {path}")


if __name__ == "__main__":
    app()
