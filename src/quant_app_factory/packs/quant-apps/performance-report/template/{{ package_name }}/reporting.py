"""Build a report: open the returns, check them, run the chosen sections, and write the files."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from . import __version__
from .context import Context
from .document import Document, TextBlock, render_html, render_markdown, to_dict
from .excel import load_workbook_table, write_workbook
from .firm.loaders import LOADERS
from .sections import available
from .settings import Settings
from .tables import Table, TableError, load_table

CONVENTIONS = [
    "Returns are simple per-bar returns, compounded geometrically.",
    "Annualised return is geometric. Volatility is the sample standard deviation, annualised.",
    "A horizon the data does not fully cover shows n/a.",
    "Every figure is computed from the returns file. The report contains no interpretation.",
]
OUTLIER = 0.5  # a per-bar move beyond this is reported as unusual


def open_returns(settings: Settings, base: Path = Path()) -> Table:
    """Load the returns file the settings name, as fractions, or the demo returns."""
    if settings.returns == "demo":
        from .synthetic import demo_returns

        return demo_returns()
    path = base / settings.returns
    return load_returns(path, settings)


def load_returns(path: Path, settings: Settings) -> Table:
    """Load ``path``: a firm's loader, a workbook, or CSV; converted to fractions."""
    if path.suffix.lower() in LOADERS:
        table = load_table(path, LOADERS)
    elif path.suffix.lower() in {".xlsx", ".xlsm"}:
        if not path.is_file():
            raise TableError(f"No file at {path}.")
        table = load_workbook_table(path, settings.excel)
    else:
        table = load_table(path)
    if settings.returns_unit == "percent":
        table = Table(table.dates, table.columns, table.values / 100.0)
    finite = table.values[np.isfinite(table.values)]
    if finite.size and float(finite.min()) < -1.0:
        hint = "" if settings.returns_unit == "percent" else '; set returns_unit = "percent"'
        raise TableError(f"{path.name} has a return below -100%{hint}.")
    return table


def build_context(table: Table, settings: Settings, as_of: dt.date | None = None) -> Context:
    """Split the benchmark from the books, cut at ``as_of``, and note anything unusual."""
    chosen = table.until(as_of) if as_of else table
    books = [name for name in chosen.columns if name != settings.benchmark]
    if settings.benchmark and settings.benchmark not in chosen.columns:
        raise TableError(
            f"No benchmark column {settings.benchmark!r}; the file has {', '.join(chosen.columns)}."
        )
    if not books:
        raise TableError("The returns file has no book besides the benchmark.")
    benchmark = chosen.column(settings.benchmark) if settings.benchmark else None
    return Context(
        books=chosen.select(books),
        settings=settings,
        benchmark=benchmark,
        benchmark_name=settings.benchmark,
        data_notes=check_data(chosen.select(books)),
    )


def check_data(table: Table) -> list[str]:
    """Report gaps inside each book's history and unusually large moves."""
    notes = []
    for name in table.columns:
        values = table.column(name)
        known = np.flatnonzero(np.isfinite(values))
        if known.size == 0:
            notes.append(f"{name} has no returns.")
            continue
        gaps = int((~np.isfinite(values[known[0] : known[-1] + 1])).sum())
        if gaps:
            notes.append(
                f"{name} has {gaps} missing returns inside its history, left out of compounding."
            )
        large = int((np.abs(values[known]) > OUTLIER).sum())
        if large:
            notes.append(
                f"{name} has {large} moves beyond {OUTLIER:.0%} in one bar. Check they are real, "
                "and that returns_unit is right."
            )
        if known[0] > 0:
            notes.append(
                f"{name} starts {table.dates[int(known[0])]}, after the file's first date."
            )
        if known[-1] < len(values) - 1:
            notes.append(f"{name} ends {table.dates[int(known[-1])]}, before the as-of date.")
    return notes


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
    return Document(
        title=f"{who}{sheet.title or name}",
        subtitle=f"{span}. Basis: {settings.basis}. Computed figures only.",
        blocks=blocks,
    )


def write_report(
    document: Document, context: Context, root: Path, *, label: str, source: str
) -> Path:
    """Write a new run folder with the HTML, Markdown, Excel, and JSON forms; return the folder."""
    created = dt.datetime.now(dt.UTC)
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "report"
    folder = _new_folder(root, f"{created:%Y%m%dT%H%M%SZ}-{slug}")
    (folder / "report.html").write_text(render_html(document), "utf-8")
    (folder / "report.md").write_text(render_markdown(document), "utf-8")
    write_workbook(document, folder / "report.xlsx")
    results: dict[str, Any] = {
        **to_dict(document),
        "provenance": {
            "created": created.isoformat(timespec="seconds"),
            "source": source,
            "data_fingerprint": f"sha256:{context.books.fingerprint()}",
            "as_of": context.as_of.isoformat(),
            "code_version": {"package": __version__, "git": _git_describe()},
        },
    }
    (folder / "results.json").write_text(
        json.dumps(_clean(results), indent=2, sort_keys=True) + "\n", "utf-8"
    )
    return folder


def _new_folder(root: Path, name: str) -> Path:
    attempt = 1
    while True:
        folder = root / (name if attempt == 1 else f"{name}-{attempt}")
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            attempt += 1


def _git_describe() -> str | None:
    try:
        described = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (described.stdout.strip() or None) if described.returncode == 0 else None


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value
