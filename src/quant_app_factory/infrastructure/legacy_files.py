"""Files for the legacy converter: the legacy sources, the inventory, and the drafted code.

Sources are read as text and never imported. The inventory is a small TOML file the developer
edits; the draft is written only where nothing would be overwritten, unless forced.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast, get_args

from quant_agent_os.tools.permissions import Permission
from quant_app_factory.domain import toml_text
from quant_app_factory.domain.legacy import (
    DataKind,
    Entry,
    Inventory,
    Kind,
    LegacyError,
    classify,
)
from quant_app_factory.domain.legacy_analysis import ModuleSource

IGNORED_DIRECTORIES = frozenset(
    {"tests", "test", "__pycache__", ".venv", "venv", "build", "dist", ".git", "node_modules"}
)
HEADER = """\
# Legacy inventory, written by `qaos legacy scan`. Edit it, then draft the code with
#   qaos legacy wrap {inventory} --out legacy_tools
#
# Per entry:
#   kind         "expose" (register as it is), "facade" (wrap in a typed function), or
#                "service" (serve behind HTTP, one process holding the state)
#   include      false leaves the entry out of the draft
#   name         the tool's name: lowercase letters, digits, and underscores
#   data         parameters that take a dataset reference: "series" or "frame"
#   permissions  what the code needs beyond reading datasets: read_local_data,
#                write_local_data, network, execute_code, external_action
#   convention   how the figure is defined (window, annualisation, units); empty leaves a TODO
# The comments above each entry are what the scan found; a rescan rewrites them.
"""


def find_sources(path: Path) -> tuple[list[ModuleSource], Path]:
    """Return the modules under ``path`` and the directory they import from.

    ``path`` is a module file, a package directory, or a directory of modules and packages.
    Tests and ``__main__`` are left out; a private module counts only for the names a public one
    imports from it. Raises `LegacyError` when there
    is no Python there.
    """
    path = path.resolve()
    if not path.exists():
        raise LegacyError(f"{path} does not exist; give a package directory or a .py file.")
    if path.is_file():
        if path.suffix != ".py":
            raise LegacyError(
                f"{path} is not a Python file; give a package directory or a .py file."
            )
        root = _import_root(path.parent)
        return [_source(path, root)], root
    root = _import_root(path) if (path / "__init__.py").exists() else path
    files = sorted(
        file
        for file in path.rglob("*.py")
        if _wanted(file.relative_to(path)) and _importable(file, root)
    )
    if not files:
        raise LegacyError(f"No Python modules under {path}.")
    return [_source(file, root) for file in files], root


def _import_root(directory: Path) -> Path:
    """The directory above the outermost package that holds ``directory``."""
    while (directory / "__init__.py").exists():
        directory = directory.parent
    return directory


def _wanted(relative: Path) -> bool:
    parts = relative.parts
    if any(part in IGNORED_DIRECTORIES or part.startswith(".") for part in parts[:-1]):
        return False
    name = relative.name
    if name in ("conftest.py", "setup.py", "__main__.py") or name.startswith("test_"):
        return False
    return not name.endswith("_test.py")


def _private(relative: Path) -> bool:
    """Whether a module is private: its own or a package's name starts with an underscore."""
    return any(part.startswith("_") and part != "__init__.py" for part in relative.parts)


def _importable(file: Path, root: Path) -> bool:
    """Whether ``file`` can be imported from ``root``: every directory between is a package."""
    directory = file.parent
    while directory != root:
        if not (directory / "__init__.py").exists():
            return False
        directory = directory.parent
    return True


def _source(file: Path, root: Path) -> ModuleSource:
    parts = list(file.relative_to(root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    try:
        text = file.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LegacyError(f"Cannot read {file}: {error}") from error
    return ModuleSource(".".join(parts), text, is_package, _private(file.relative_to(root)))


def dump_inventory(inventory: Inventory, path: Path) -> str:
    """Return the inventory as TOML, with the source relative to ``path``'s directory."""
    relative = Path(os.path.relpath(inventory.source, path.resolve().parent))
    near = relative.parts[:3].count("..") < 3  # beside the inventory, not across the disk
    source = (relative if near else inventory.source).as_posix()
    lines = [HEADER.format(inventory=path.name)]
    lines += [f"source = {toml_text.value(source)}", f"app = {toml_text.value(inventory.app)}", ""]
    if inventory.skipped:
        lines += ["# Skipped:", *(f"#   {reason}" for reason in inventory.skipped), ""]
    for entry in inventory.entries:
        found = inventory.found.get(entry.target)
        if found is not None:
            _, reasons = classify(found)
            lines.append(f"# {found.signature}")
            lines += [f"#   {reason}" for reason in reasons]
        data = ", ".join(
            f"{toml_text.key(k)} = {toml_text.value(v)}" for k, v in entry.data.items()
        )
        lines += [
            "[[entry]]",
            f"target = {toml_text.value(entry.target)}",
            f"kind = {toml_text.value(entry.kind.value)}",
            f"include = {toml_text.value(entry.include)}",
            f"name = {toml_text.value(entry.name)}",
            f"data = {{ {data} }}" if data else "data = {}",
            f"permissions = {toml_text.value([p.value for p in entry.permissions])}",
            f"convention = {toml_text.value(entry.convention)}",
            "",
        ]
    return "\n".join(lines)


def write_inventory(inventory: Inventory, path: Path, *, force: bool = False) -> None:
    """Write the inventory to ``path``; refuse to replace one, and its edits, unless forced."""
    if path.exists() and not force:
        raise LegacyError(
            f"{path} exists and may hold your edits; pass --force to replace it, or write "
            "another file."
        )
    path.write_text(dump_inventory(inventory, path), "utf-8")


def load_inventory(path: Path) -> Inventory:
    """Read an inventory file, or raise `LegacyError` naming what to fix."""
    try:
        document = tomllib.loads(path.read_text("utf-8"))
    except OSError as error:
        raise LegacyError(f"Cannot read {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise LegacyError(f"{path} is not valid TOML: {error}") from error
    source = document.get("source")
    if not isinstance(source, str) or not source:
        raise LegacyError(f"{path} needs source = the scanned package, relative to the file.")
    app = document.get("app", "legacy")
    entries = [
        _entry(path, number, item) for number, item in enumerate(document.get("entry", []), 1)
    ]
    names = [entry.name for entry in entries if entry.include]
    twice = sorted({name for name in names if names.count(name) > 1})
    if twice:
        raise LegacyError(f"{path}: more than one included entry is named {', '.join(twice)}.")
    return Inventory(
        source=(path.resolve().parent / source).resolve(), app=str(app), entries=entries
    )


def _entry(path: Path, number: int, item: Any) -> Entry:
    where = f"{path}, entry {number}"
    if not isinstance(item, dict) or not isinstance(item.get("target"), str):
        raise LegacyError(f"{where} needs target = the dotted name scanned.")
    try:
        kind = Kind(item.get("kind", "facade"))
    except ValueError:
        raise LegacyError(f"{where}: kind must be expose, facade, or service.") from None
    data = item.get("data", {})
    if not isinstance(data, dict) or not all(
        value in get_args(DataKind) for value in data.values()
    ):
        raise LegacyError(f'{where}: data maps parameter names to "series" or "frame".')
    try:
        permissions = [Permission(value) for value in item.get("permissions", [])]
    except ValueError as error:
        known = ", ".join(permission.value for permission in Permission)
        raise LegacyError(f"{where}: {error}; permissions are {known}.") from None
    name = item.get("name") or item["target"].rsplit(".", 1)[-1]
    return Entry(
        target=item["target"],
        kind=kind,
        name=str(name),
        include=bool(item.get("include", True)),
        data=cast(dict[str, DataKind], data),
        permissions=permissions,
        convention=str(item.get("convention", "")),
    )


def write_draft(out: Path, files: dict[str, str], *, force: bool = False) -> list[Path]:
    """Write ``files`` under ``out`` and return their paths; refuse to overwrite unless forced."""
    targets = {out / name: text for name, text in files.items()}
    existing = sorted(str(target) for target in targets if target.exists())
    if existing and not force:
        raise LegacyError(
            f"Would overwrite {', '.join(existing)}; pass --force, or choose another --out."
        )
    for target, text in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, "utf-8")
    return list(targets)
