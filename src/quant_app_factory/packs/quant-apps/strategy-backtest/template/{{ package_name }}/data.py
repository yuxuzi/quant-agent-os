"""Price panels: prices by date and asset, read through the shared table format."""

from __future__ import annotations

from pathlib import Path

from quant_toolbox.data import Panel, TableError, aligned, demo_panel, load_table

from .firm.loaders import LOADERS

DataError = TableError
__all__ = ["DataError", "Panel", "aligned", "open_panel"]


def open_panel(source: str, base: Path = Path()) -> Panel:
    """Load what a ``data`` setting names: "demo" or a price file, relative to ``base``."""
    if source == "demo":
        return demo_panel()
    path = base / source
    if not path.is_file():
        raise DataError(f"No price file at {path}; set data in config.toml or pass --prices.")
    return Panel.from_table(load_table(path, LOADERS))
