"""Results: one folder per run, with what it computed and what it was computed from.

A run folder, ``results/<UTC time>-<label>/``, holds ``results.json`` (metrics, series,
parameters, and provenance: a fingerprint of the price data, the code version, and the time)
and ``config.toml``, the exact parameters, so ``--config results/<run>/config.toml`` repeats
the run. Runs never overwrite each other.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .parameters import Parameters, dump_parameters

SCHEMA_VERSION = 2
READABLE_VERSIONS = frozenset({1, 2})  # version 1: a single results file, no provenance
RESULTS_FILE = "results.json"


def save_results(path: Path, results: dict[str, Any]) -> Path:
    """Write ``results`` as JSON (NaN becomes null) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, **results}
    path.write_text(json.dumps(_clean(payload), indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def load_results(path: Path) -> dict[str, Any]:
    """Read a run folder, or a results file written by `save_results`."""
    file = path / RESULTS_FILE if path.is_dir() else path
    try:
        payload = json.loads(file.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read results from {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") not in READABLE_VERSIONS:
        raise ValueError(f"{file} is not a results file this project can read.")
    return payload


def save_run(
    root: Path,
    results: dict[str, Any],
    *,
    parameters: Parameters,
    fingerprint: str,
    label: str | None = None,
    now: dt.datetime | None = None,
) -> Path:
    """Write a new run folder under ``root`` and return it; an existing folder is never reused."""
    created = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    name = f"{created:%Y%m%dT%H%M%SZ}-{_slug(label or _default_label(results))}"
    attempt = 1
    while True:
        folder = root / (name if attempt == 1 else f"{name}-{attempt}")
        try:
            folder.mkdir(parents=True)
            break
        except FileExistsError:
            attempt += 1
    provenance = {
        "created": created.isoformat(timespec="seconds"),
        "data_fingerprint": f"sha256:{fingerprint}",
        "code_version": code_version(),
    }
    save_results(folder / RESULTS_FILE, {**results, "provenance": provenance})
    (folder / "config.toml").write_text(dump_parameters(parameters), "utf-8")
    return folder


def latest_run(root: Path) -> Path:
    """The newest run folder under ``root``: by the time in its name, then by write time."""
    runs = [path.parent for path in root.glob(f"*/{RESULTS_FILE}")]
    if not runs:
        raise ValueError(f"No runs under {root}; run `study` or `backtest` first.")
    return max(runs, key=lambda run: (run.name[:16], (run / RESULTS_FILE).stat().st_mtime_ns))


def code_version() -> dict[str, str | None]:
    """This package's version and, when it lives in a git checkout, ``git describe``."""
    return {"package": __version__, "git": _git_describe()}


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
    if described.returncode != 0:
        return None
    return described.stdout.strip() or None


def _default_label(results: dict[str, Any]) -> str:
    signal = (results.get("parameters") or {}).get("signal", "")
    return f"{results.get('kind', 'run')}-{signal}".strip("-")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "run"


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value
