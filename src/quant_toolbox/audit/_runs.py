"""Run folders and provenance: what a run computed, from what, with which code, and when.

A run folder, ``<root>/<UTC time>-<label>/``, is never reused: a second run in the same second
gets ``-2``. Results are JSON with NaN written as null. Provenance records each input's
fingerprint, the code version (a package version and, in a git checkout, ``git describe``), and
the time.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

RESULTS_FILE = "results.json"


def git_describe(where: Path) -> str | None:
    """``git describe --always --dirty`` for the checkout holding ``where``, or None."""
    folder = where if where.is_dir() else where.parent
    try:
        described = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (described.stdout.strip() or None) if described.returncode == 0 else None


def code_version(version: str, where: Path) -> dict[str, str | None]:
    """A package's ``version`` and, when ``where`` is in a git checkout, ``git describe``."""
    return {"package": version, "git": git_describe(where)}


def slug(text: str, default: str = "run") -> str:
    """``text`` as a short, safe file name part: lower case, letters, digits, and dashes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or default


def run_name(label: str, now: dt.datetime | None = None) -> str:
    """A folder name ``<UTC time>-<label>`` that sorts by time."""
    created = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    return f"{created:%Y%m%dT%H%M%SZ}-{slug(label)}"


def new_run_folder(root: Path, name: str) -> Path:
    """Create ``root/name`` (or ``name-2``, ``name-3``, ... when taken) and return it."""
    attempt = 1
    while True:
        folder = root / (name if attempt == 1 else f"{name}-{attempt}")
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            attempt += 1


def json_safe(value: Any) -> Any:
    """``value`` with every non-finite float as None, through dicts, lists, and tuples."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return value


def write_json(path: Path, payload: Any) -> Path:
    """Write ``payload`` as sorted, indented JSON (NaN becomes null) and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n", "utf-8")
    return path


def provenance(
    *,
    version: str,
    where: Path,
    fingerprints: dict[str, str] | None = None,
    now: dt.datetime | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """When a run happened, from which inputs (``sha256:`` fingerprints), and with which code."""
    created = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    return {
        "created": created.isoformat(timespec="seconds"),
        **{name: f"sha256:{value}" for name, value in (fingerprints or {}).items()},
        **extra,
        "code_version": code_version(version, where),
    }


def latest_run(root: Path, results_file: str = RESULTS_FILE, hint: str = "") -> Path:
    """The newest run folder under ``root``: by the time in its name, then by write time."""
    runs = [path.parent for path in root.glob(f"*/{results_file}")]
    if not runs:
        raise ValueError(f"No runs under {root}{'; ' + hint if hint else ''}.")
    return max(runs, key=lambda run: (run.name[:16], (run / results_file).stat().st_mtime_ns))
