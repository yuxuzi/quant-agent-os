"""Build a report: open the returns, check them, run the chosen sections, and write the files."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from quant_toolbox import audit
from quant_toolbox.data import (
    STALE_PERIODS,
    Frequency,
    Table,
    TableError,
    check_frequency,
    demo_returns,
    infer_frequency,
    load_table,
    periods_behind,
    return_notes,
)
from quant_toolbox.data.excel import read_workbook
from quant_toolbox.report import Document, TextBlock, render_html, render_markdown, to_dict
from quant_toolbox.report.excel import write_workbook

from . import __version__
from .context import Context
from .firm.loaders import LOADERS
from .reconcile import Reconciliation
from .sections import available
from .settings import AUTO, Settings

CONVENTIONS = [
    "Returns are simple per-bar returns, compounded geometrically.",
    "Annualised return is geometric. Volatility is the sample standard deviation, annualised.",
    "A horizon the data does not fully cover, or is shorter than its periods, shows n/a.",
    "Every figure is computed from the returns file. The report contains no interpretation.",
]


def open_returns(settings: Settings, base: Path = Path()) -> Table:
    """Load the returns file the settings name, as fractions, or the demo returns."""
    if settings.returns == "demo":
        return demo_returns()
    path = base / settings.returns
    return load_returns(path, settings)


def load_returns(path: Path, settings: Settings, *, reference: bool = False) -> Table:
    """Load ``path``: a firm's loader, a workbook, or CSV; converted to fractions.

    With ``reference``, the ``[reference]`` settings give the unit and the workbook layout, and
    fall back to the returns file's own.
    """
    excel = (settings.reference.excel if reference else None) or settings.excel
    unit = (settings.reference.unit if reference else "") or settings.returns_unit
    if path.suffix.lower() in LOADERS:
        table = load_table(path, LOADERS)
    elif path.suffix.lower() in {".xlsx", ".xlsm"}:
        if not path.is_file():
            raise TableError(f"No file at {path}.")
        try:
            table = read_workbook(path, sheet=excel.sheet, header_row=excel.header_row)
        except TableError as error:
            if not reference:
                raise
            # The reference file has its own layout, so point at its own settings.
            raise TableError(str(error).replace("[excel]", "[reference]")) from error
    else:
        table = load_table(path)
    if unit == "percent":
        table = Table(table.dates, table.columns, table.values / 100.0)
    finite = table.values[np.isfinite(table.values)]
    if finite.size and float(finite.min()) < -1.0:
        setting = 'unit = "percent" under [reference]' if reference else 'returns_unit = "percent"'
        hint = "" if unit == "percent" else f"; set {setting}"
        raise TableError(f"{path.name} has a return below -100%{hint}.")
    return table


def build_context(table: Table, settings: Settings, as_of: dt.date | None = None) -> Context:
    """Split the benchmark from the books, cut at ``as_of``, and note anything unusual.

    The periods a year come from the whole file's dates, or the settings' number, which must agree
    with them; the context's settings then hold that count.
    """
    auto = settings.periods_per_year == AUTO
    if auto:
        periods = infer_frequency(table.dates)
    else:
        periods = check_frequency(table.dates, int(settings.periods_per_year))
    settings = dataclasses.replace(settings, periods_per_year=periods.periods_per_year)
    chosen = table.until(as_of) if as_of else table
    bench_columns = settings.benchmark_columns()
    missing = [name for name in bench_columns if name not in chosen.columns]
    if missing:
        raise TableError(
            f"No benchmark column {missing[0]!r}; the file has {', '.join(chosen.columns)}."
        )
    books = [name for name in chosen.columns if name not in bench_columns]
    if not books:
        raise TableError("The returns file has no book besides the benchmark.")
    if isinstance(settings.benchmark, dict):
        unknown = [book for book in settings.benchmark if book not in books]
        if unknown:
            raise TableError(
                f"[benchmark] names {', '.join(unknown)}, which is not a book in the file; "
                f"the books are {', '.join(books)}."
            )
        benchmarks = {
            book: settings.benchmark[book] for book in books if book in settings.benchmark
        }
    else:
        benchmarks = {book: settings.benchmark for book in books} if settings.benchmark else {}
    notes = check_data(chosen.select(books))
    unset = [book for book in books if book not in benchmarks]
    if isinstance(settings.benchmark, dict) and unset:
        notes.append(
            f"No benchmark is set for {', '.join(unset)} under [benchmark]; their "
            "benchmark-relative figures are n/a."
        )
    return Context(
        books=chosen.select(books),
        settings=settings,
        benchmarks=benchmarks,
        benchmark_returns={name: chosen.column(name) for name in bench_columns},
        data_notes=notes + staleness_notes(chosen.dates[-1], as_of or dt.date.today(), periods),
        periods=periods,
        periods_how="inferred from the dates" if auto else "set in config.toml",
    )


def staleness_notes(last: dt.date, as_of: dt.date, periods: Frequency) -> list[str]:
    """A note when the last date is more than two periods before ``as_of``, naming both."""
    behind = periods_behind(last, as_of, periods)
    if behind <= STALE_PERIODS:
        return []
    unit = "business days" if periods.name == "daily" else "periods"
    return [
        f"The last date is {last}, {(as_of - last).days} days ({behind:.1f} {unit}) before the "
        f"as-of date {as_of}. The figures stop at {last}."
    ]


def _periods_statement(periods: Frequency, how: str) -> str:
    """The periods, as a report states them, such as "12 periods a year (monthly, ...)"."""
    named = f"{periods.name}, " if periods.name else ""
    return f"{periods.periods_per_year} periods a year ({named}{how})"


def check_data(table: Table) -> list[str]:
    """Report gaps inside each book's history and unusually large moves."""
    return return_notes(table)


def build_document(context: Context, tearsheet: str | None = None) -> Document:
    """Run the sections of ``tearsheet`` (default: the settings' default) into a document."""
    settings = context.settings
    name = tearsheet or settings.default_tearsheet
    if name not in settings.tearsheets:
        raise TableError(
            f"No tear sheet {name!r}; config.toml has {', '.join(sorted(settings.tearsheets))}."
        )
    sheet = settings.tearsheets[name]
    sections = available()
    unknown = [section for section in sheet.sections if section not in sections]
    if unknown:
        raise TableError(
            f"Tear sheet {name!r} names unknown sections {', '.join(unknown)}; "
            f"available: {', '.join(sorted(sections))}."
        )
    blocks = [block for section in sheet.sections for block in sections[section](context)]
    if context.data_notes and "data_notes" not in sheet.sections:
        blocks.append(TextBlock(title="Data notes", lines=context.data_notes))
    blocks.append(TextBlock(title="Conventions", lines=CONVENTIONS))
    who = f"{settings.firm_name}: " if settings.firm_name else ""
    span = f"{context.dates[0]} to {context.as_of}"
    periods = (
        _periods_statement(context.periods, context.periods_how)
        if context.periods
        else f"{context.periods_per_year} periods"
    )
    return Document(
        title=f"{who}{sheet.title or name}",
        subtitle=f"{span}. Basis: {settings.basis}. {periods}. Computed figures only.",
        blocks=blocks,
    )


def write_reconciliation(
    result: Reconciliation,
    returns: Table,
    reference: Path,
    settings: Settings,
    root: Path,
    *,
    source: str,
) -> Path:
    """Write a run folder with ``reconcile.json``: the comparison, its breaks, and the reference.

    Each run gets its own folder, so a restatement after a report went out leaves a dated record
    of the reference that was compared, by name and SHA-256, beside the report's own folder.
    """
    created = dt.datetime.now(dt.UTC)
    folder = audit.new_run_folder(root, audit.run_name("reconcile", created))
    digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    months = {item.month for item in result.compared}
    payload: dict[str, Any] = {
        "reference": {
            "file": reference.name,
            "fingerprint": f"sha256:{digest}",
            "unit": settings.reference.unit or settings.returns_unit,
        },
        "reconciliation": {
            "summary": result.summary,
            "clean": result.clean,
            "tolerance_bps": result.tolerance_bps,
            "comparisons": len(result.compared),
            "periods": len(months),
            "period_name": result.noun,
            "books": len({item.book for item in result.compared}),
            "breaks": [
                {
                    "book": item.book,
                    "month": item.month,
                    "ours": item.ours,
                    "reference": item.reference,
                    "bps": item.bps,
                }
                for item in result.breaks
            ],
            "only_ours": result.only_ours,
            "only_reference": result.only_reference,
        },
        "provenance": audit.provenance(
            version=__version__,
            where=Path(__file__),
            fingerprints={"data_fingerprint": returns.fingerprint()},
            now=created,
            source=source,
        ),
    }
    audit.write_json(folder / "reconcile.json", payload)
    return folder


def write_report(
    document: Document, context: Context, root: Path, *, label: str, source: str
) -> Path:
    """Write a new run folder with the HTML, Markdown, Excel, and JSON forms; return the folder."""
    created = dt.datetime.now(dt.UTC)
    folder = audit.new_run_folder(root, audit.run_name(label or "report", created))
    charts = context.settings.charts
    (folder / "report.html").write_text(render_html(document, charts=charts), "utf-8")
    (folder / "report.md").write_text(render_markdown(document), "utf-8")
    write_workbook(document, folder / "report.xlsx")
    results: dict[str, Any] = {
        **to_dict(document),
        "provenance": audit.provenance(
            version=__version__,
            where=Path(__file__),
            fingerprints={"data_fingerprint": context.books.fingerprint()},
            now=created,
            source=source,
            as_of=context.as_of.isoformat(),
        ),
    }
    audit.write_json(folder / "results.json", results)
    return folder
