"""Write the small TOML documents App Factory produces: specs and the settings file."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def key(name: str) -> str:
    """Return ``name`` as a TOML key, quoted when it is not a bare key."""
    return name if _BARE_KEY.match(name) else json.dumps(name)


def value(item: Any) -> str:
    """Return a string, boolean, integer, or list of strings as TOML (JSON strings are TOML)."""
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int | float):
        return str(item)
    if isinstance(item, list | tuple):
        return json.dumps([str(element) for element in item])
    return json.dumps(str(item))


def table(name: str | None, entries: Mapping[str, Any]) -> list[str]:
    """Return the lines of one table; ``None`` values are left out, and so is an empty table."""
    lines = [f"{key(field)} = {value(item)}" for field, item in entries.items() if item is not None]
    if not lines:
        return []
    return ([f"[{name}]"] if name else []) + lines + [""]
