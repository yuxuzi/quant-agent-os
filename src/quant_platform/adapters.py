"""Stub adapters (decision 0037): a typed file that maps a fund's source to a standard table.

`write_stub` writes ``NAME_adapter.py``, a class whose ``read`` raises `NotImplementedError` with a
TODO naming the table's required columns, and ``test_NAME_adapter.py``, which calls it and checks
the result with `quant_toolbox.data.model.validate`. The test fails until ``read`` is filled in.
Nothing is overwritten unless ``force`` is set.
"""

from __future__ import annotations

import re
from pathlib import Path

from quant_toolbox.data.model import TABLES, Column, TableSpec

_NAME = re.compile(r"[a-z][a-z0-9_]*")


class AdapterError(ValueError):
    """Raised when a stub cannot be written; the message says what to change."""


def write_stub(name: str, table: str, out_dir: Path, *, force: bool = False) -> list[Path]:
    """Write the adapter and its test for standard ``table`` and return the two paths.

    Raises `AdapterError`, writing nothing, when the name or table is wrong, or when either file
    exists and ``force`` is false.
    """
    if not _NAME.fullmatch(name):
        raise AdapterError(
            f"Adapter name {name!r} must be lower case letters, digits, and underscores, "
            "starting with a letter."
        )
    spec = TABLES.get(table)
    if spec is None:
        raise AdapterError(f"No standard table {table!r}; choose one of {', '.join(TABLES)}.")
    adapter_path = out_dir / f"{name}_adapter.py"
    test_path = out_dir / f"test_{name}_adapter.py"
    if not force:
        taken = [path for path in (adapter_path, test_path) if path.exists()]
        if taken:
            listed = ", ".join(str(path) for path in taken)
            raise AdapterError(f"{listed} already exists; pass --force to overwrite it.")
    out_dir.mkdir(parents=True, exist_ok=True)
    adapter_path.write_text(_adapter_source(name, spec), encoding="utf-8")
    test_path.write_text(_test_source(name, spec), encoding="utf-8")
    return [adapter_path, test_path]


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Adapter"


def _column_lines(spec: TableSpec) -> str:
    lines = []
    for column in spec.columns:
        lines.append(f"        {_describe(column)}")
    return "\n".join(lines)


def _describe(column: Column) -> str:
    status = "required" if column.required else "optional"
    return f"- {column.name}: {column.type}, {status}"


def _required(spec: TableSpec) -> str:
    return ", ".join(column.name for column in spec.columns if column.required)


def _adapter_source(name: str, spec: TableSpec) -> str:
    cls = _class_name(name)
    return f'''"""Adapter for the standard `{spec.name}` table: fill `read` from the fund's source.

{spec.doc}

Fill `read` so it returns one list per column, all the same length, with one value per row. Dates
are ``datetime.date`` values; a missing optional value is None. Key: {", ".join(spec.key)}.
Check the result with `quant_toolbox.data.model.validate("{spec.name}", columns)`; the test next to
this file does that.
"""

from __future__ import annotations

import datetime as dt


class {cls}:
    """Reads the standard `{spec.name}` table from the fund's own source. Fill `read`."""

    def read(self, start: dt.date, end: dt.date) -> dict[str, list]:
        """Return the `{spec.name}` rows dated from ``start`` to ``end`` as columns.

        Columns:
{_column_lines(spec)}
        """
        raise NotImplementedError(
            "TODO: fill {cls}.read from the fund's source. "
            "Required columns of {spec.name}: {_required(spec)}."
        )
'''


def _test_source(name: str, spec: TableSpec) -> str:
    cls = _class_name(name)
    return f'''"""Checks the `{spec.name}` adapter: this fails until `{cls}.read` is filled in."""

import datetime as dt

from quant_toolbox.data.model import validate

from {name}_adapter import {cls}


def test_{name}_reads_a_valid_{spec.name}_table() -> None:
    columns = {cls}().read(dt.date(2024, 1, 1), dt.date(2024, 1, 31))

    assert validate("{spec.name}", columns) == []
'''
