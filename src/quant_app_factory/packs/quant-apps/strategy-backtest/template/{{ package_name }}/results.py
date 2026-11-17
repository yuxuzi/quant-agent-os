"""Results: one folder per run, with what it computed and what it was computed from.

A run folder, ``results/<UTC time>-<label>/``, holds ``results.json`` (metrics, series,
parameters, and provenance: a fingerprint of the price data, the code version, and the time)
and ``config.toml``, the exact parameters, so ``--config results/<run>/config.toml`` repeats
the run. Runs never overwrite each other.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from quant_toolbox import audit

from . import __version__
from .parameters import Parameters, dump_parameters

SCHEMA_VERSION = 2
READABLE_VERSIONS = frozenset({1, 2})  # version 1: a single results file, no provenance
RESULTS_FILE = audit.RESULTS_FILE


def save_results(path: Path, results: dict[str, Any]) -> Path:
    """Write ``results`` as JSON (NaN becomes null) and return the path."""
    return audit.write_json(path, {"schema_version": SCHEMA_VERSION, **results})


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
    folder = audit.new_run_folder(root, audit.run_name(label or _default_label(results), created))
    provenance = audit.provenance(
        version=__version__,
        where=Path(__file__),
        fingerprints={"data_fingerprint": fingerprint},
        now=created,
    )
    save_results(folder / RESULTS_FILE, {**results, "provenance": provenance})
    (folder / "config.toml").write_text(dump_parameters(parameters), "utf-8")
    return folder


def latest_run(root: Path) -> Path:
    """The newest run folder under ``root``: by the time in its name, then by write time."""
    return audit.latest_run(root, RESULTS_FILE, hint="run `study` or `backtest` first")


def code_version() -> dict[str, str | None]:
    """This package's version and, when it lives in a git checkout, ``git describe``."""
    return audit.code_version(__version__, Path(__file__))


def _default_label(results: dict[str, Any]) -> str:
    signal = (results.get("parameters") or {}).get("signal", "")
    return f"{results.get('kind', 'run')}-{signal}".strip("-")
