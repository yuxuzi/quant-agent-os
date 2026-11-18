"""Scan legacy code into an inventory, and draft tools from an edited inventory (decision 0029).

Both steps run on the developer's machine. Neither imports or runs the legacy code, and the
server takes no part: what the draft registers is what the firm commits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from quant_app_factory.domain.legacy import Inventory, Kind, LegacyError, suggest
from quant_app_factory.domain.legacy_analysis import analyse
from quant_app_factory.domain.legacy_draft import Draft, describe_next_steps, draft
from quant_app_factory.infrastructure.legacy_files import find_sources, load_inventory, write_draft


@dataclass(frozen=True)
class Wrapped:
    """What `wrap` wrote, the decisions it left open, and what to do next."""

    written: list[Path]
    draft: Draft
    next_steps: list[str]


def scan(path: Path, *, app: str = "legacy") -> Inventory:
    """Inventory the public functions and methods under ``path``, without importing them.

    Raises `LegacyError` when ``path`` holds no Python, or nothing public is found.
    """
    sources, _ = find_sources(path)
    found, skipped = analyse(sources)
    if not found:
        raise LegacyError(f"No public functions or methods found under {path}.")
    return Inventory(
        source=path.resolve(),
        app=app,
        entries=suggest(found),
        found={item.target: item for item in found},
        skipped=skipped,
    )


def wrap(inventory_path: Path, out: Path, *, force: bool = False) -> Wrapped:
    """Draft facades, a server file, a service, and a test into ``out`` from an inventory.

    The source is scanned again, so the draft follows the code as it is now, with the
    developer's decisions from the inventory. Nothing is overwritten unless ``force``. Raises
    `LegacyError` naming what to fix.
    """
    inventory = load_inventory(inventory_path)
    sources, root = find_sources(inventory.source)
    found, _ = analyse(sources)
    legacy = sources[0].name.split(".")[0]
    relative = Path(os.path.relpath(root, out.resolve()))
    near = relative.parts[:3].count("..") < 3  # beside the draft, not across the disk
    source_root = (relative if near else root).as_posix()
    result = draft(
        inventory,
        {item.target: item for item in found},
        source_root=source_root,
        legacy=legacy,
    )
    written = write_draft(out, result.files, force=force)
    services = any(e.include and e.kind is Kind.SERVICE for e in inventory.entries)
    return Wrapped(written, result, describe_next_steps(str(out), result, services))
