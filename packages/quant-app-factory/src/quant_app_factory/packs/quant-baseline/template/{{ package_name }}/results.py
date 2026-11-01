"""Results files: what a study or backtest writes and what a report reads (JSON, version 1)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def save_results(path: Path, results: dict[str, Any]) -> Path:
    """Write ``results`` as JSON (NaN becomes null) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **results}
    path.write_text(json.dumps(_clean(payload), indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def load_results(path: Path) -> dict[str, Any]:
    """Read a results file written by `save_results`."""
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a version {SCHEMA_VERSION} results file.")
    return payload


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value
