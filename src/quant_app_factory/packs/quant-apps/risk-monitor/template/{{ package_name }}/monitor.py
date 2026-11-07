"""Build a risk report: open the data, run the chosen sections, and write the files."""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .document import Document, TextBlock, render_html, render_markdown, to_dict
from .firm.loaders import LOADERS
from .risk import RiskView, build_view
from .sections import available
from .settings import Settings
from .tables import Table, TableError, load_table

CONVENTIONS = [
    "Positions are weights of NAV, with shorts negative.",
    "Return-based risk applies today's weights to past asset returns. It is not the fund's record.",
    "A stress applies each scenario's return to today's weights, linearly.",
    "Every figure is computed from your files. The report contains no interpretation or advice.",
]


def open_view(
    settings: Settings,
    base: Path = Path(),
    *,
    positions: Path | None = None,
    as_of: dt.date | None = None,
) -> RiskView:
    """Load the settings' files into a `RiskView`, or the demo when they say so."""
    if positions is None and settings.positions == "demo":
        from .synthetic import demo_book

        book, prices, groups = demo_book()
        return build_view(book, settings, prices=prices, groups=groups, as_of=as_of)
    table = load_table(positions or base / settings.positions, LOADERS)
    prices_table = load_table(base / settings.prices, LOADERS) if settings.prices else None
    groups_map = load_groups(base / settings.groups) if settings.groups else None
    return build_view(table, settings, prices=prices_table, groups=groups_map, as_of=as_of)


def load_groups(path: Path) -> dict[str, str]:
    """Read a CSV with the columns ``asset`` and ``group``."""
    if not path.is_file():
        raise TableError(f"No groups file at {path}.")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or {"asset", "group"} - set(reader.fieldnames):
            raise TableError(f"{path} needs the columns asset and group.")
        return {row["asset"].strip(): row["group"].strip() for row in reader}


def build_document(view: RiskView) -> Document:
    """Run the settings' sections into a document."""
    sections = available()
    chosen = view.settings.sections
    unknown = [name for name in chosen if name not in sections]
    if unknown:
        raise TableError(
            f"config.toml names unknown sections {', '.join(unknown)}; "
            f"available: {', '.join(sorted(sections))}."
        )
    blocks = [block for name in chosen for block in sections[name](view)]
    if view.notes and "notes" not in chosen:
        blocks.append(TextBlock("Data notes", view.notes))
    blocks.append(TextBlock("Conventions", CONVENTIONS))
    firm = f"{view.settings.firm_name}: " if view.settings.firm_name else ""
    return Document(
        title=f"{firm}Risk monitor",
        subtitle=f"As of {view.as_of}. Computed figures only.",
        blocks=blocks,
    )


def fingerprint(table: Table) -> str:
    """The provenance form of a table's fingerprint."""
    return f"sha256:{table.fingerprint()}"


def write_report(document: Document, view: RiskView, root: Path, *, source: str) -> Path:
    """Write a new run folder with the HTML, Markdown, and JSON forms; return the folder."""
    created = dt.datetime.now(dt.UTC)
    stamp = re.sub(r"[^0-9TZ]", "", created.strftime("%Y%m%dT%H%M%SZ"))
    folder = _new_folder(root, f"{stamp}-risk")
    (folder / "report.html").write_text(render_html(document), "utf-8")
    (folder / "report.md").write_text(render_markdown(document), "utf-8")
    results: dict[str, Any] = {
        **to_dict(document),
        "provenance": {
            "created": created.isoformat(timespec="seconds"),
            "source": source,
            "positions_fingerprint": fingerprint(view.history),
            "as_of": view.as_of.isoformat(),
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
