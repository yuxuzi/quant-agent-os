"""Template pack files: fork a built-in profile into a user pack, or fetch a pinned git pack.

Forking works on the built-in packs, whose layout App Factory controls: it keeps one profile,
the components that profile can use, and the questions; renames the pack (and optionally the
profile); and leaves the result for the user to edit by hand or with an AI assistant.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quant_agent_os.runtime.runner import CommandResult, run_command
from quant_app_factory.domain.spec import TEMPLATE_NAME

CLONE_TIMEOUT_SECONDS = 300.0


class PackError(ValueError):
    """Raised when a pack cannot be forked or fetched; the message says what to change."""


def fork_pack(
    source: Path, profile: str, destination: Path, *, pack_id: str, profile_id: str | None = None
) -> Path:
    """Copy ``profile`` of the built-in pack at ``source`` into a new pack at ``destination``."""
    new_profile = profile_id or profile
    for label, value in (("pack id", pack_id), ("profile id", new_profile)):
        if not TEMPLATE_NAME.match(value):
            raise PackError(f"The {label} {value!r} must be a lowercase slug, such as my-backtest.")
    if destination.exists():
        raise PackError(f"{destination} already exists; choose a new directory.")
    profiles = sorted(p.name for p in source.iterdir() if (p / "profile.toml").is_file())
    if profile not in profiles:
        raise PackError(
            f"{source.name} has no profile {profile!r}; it offers {', '.join(profiles)}."
        )
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for other in profiles:
        if other != profile:
            shutil.rmtree(destination / other)
    if new_profile != profile:
        (destination / profile).rename(destination / new_profile)
        _rename_references(destination, profile, new_profile)
    profile_file = destination / new_profile / "profile.toml"
    profile_file.write_text(
        re.sub(r'(?m)^id = ".*"$', f'id = "{new_profile}"', profile_file.read_text("utf-8")),
        "utf-8",
    )
    kept = _prune_components(destination, profile, new_profile)
    questions = destination / "questions.yml"
    text = _replace_choices(questions.read_text("utf-8"), "profile", [new_profile])
    questions.write_text(_replace_choices(text, "components", sorted(kept)), "utf-8")
    (destination / "pytemplate-pack.toml").write_text(
        "contract = 1\n"
        f'id = "{pack_id}"\n'
        f'name = "{pack_id}"\n'
        f'description = "Forked from {source.name}/{profile}; edit it to make it yours."\n'
        'version = "0.1.0"\n',
        "utf-8",
    )
    return destination


@dataclass(frozen=True)
class FetchedPack:
    """A git pack in the local cache. ``commit`` is None when an existing cache was reused."""

    path: Path
    commit: str | None
    digest: str


def pack_digest(root: Path) -> str:
    """Return a SHA-256 over every file's relative path and bytes under ``root``.

    This is the digest pytemplate-uv records and its ``--template-digest`` takes: files in order
    of their path components, each path (with forward slashes) and each file's bytes followed by
    a NUL byte. ``tests/factory/test_template_packs.py`` holds the two equal.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).parts):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def clone_pack(
    source: str,
    ref: str,
    destination: Path,
    runner: Callable[..., CommandResult] = run_command,
    *,
    commit: str | None = None,
    digest: str | None = None,
) -> FetchedPack:
    """Fetch ``source`` (``gh:org/repo``, a git URL, or a local repository) at ``ref``.

    The checkout is cached at ``destination`` without its git history; an existing cache is
    reused, so plans never need the network again. ``commit`` and ``digest`` pin the pack: a
    fetch that resolves to another commit, or whose files have another digest, is removed and
    refused. A reused cache has no commit to compare, so pass ``commit`` only for a new fetch.
    """
    if destination.is_dir():
        fetched = FetchedPack(destination, None, pack_digest(destination))
    else:
        fetched = _fetch(source, ref, destination, runner)
    problem = None
    if commit is not None and fetched.commit != commit:
        problem = f"is commit {fetched.commit or 'unknown'}, not the pinned {commit}"
    elif digest is not None and fetched.digest != digest:
        problem = f"has digest {fetched.digest}, not the pinned {digest}"
    if problem:
        _remove_tree(destination)
        raise PackError(
            f"{source} at {ref} {problem}. The tag may have moved; nothing was registered."
        )
    return fetched


def _fetch(
    source: str, ref: str, destination: Path, runner: Callable[..., CommandResult]
) -> FetchedPack:
    url = f"https://github.com/{source[3:]}.git" if source.startswith("gh:") else source
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Line endings are not converted, so a pack has the same bytes and digest on every machine.
    clone = ["git", "-c", "core.autocrlf=false", "clone", "--quiet", "--depth", "1"]
    result = runner(
        [*clone, "--branch", ref, url, str(destination)],
        cwd=destination.parent,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if result.ok:
        result = runner(["git", "rev-parse", "HEAD"], cwd=destination, timeout=30.0)
    if not result.ok:
        _remove_tree(destination)
        detail = (result.stderr_tail or result.stdout_tail).strip()[-500:]
        raise PackError(f"Cannot fetch {source} at {ref}: {detail or 'git failed'}")
    _remove_tree(destination / ".git")
    return FetchedPack(destination, result.stdout_tail.strip(), pack_digest(destination))


def _remove_tree(path: Path) -> None:
    """Delete ``path``, including files git marks read-only (as it does on Windows)."""

    def make_writable(function: Callable[..., object], target: str, _: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=make_writable)


def _prune_components(pack: Path, profile: str, new_profile: str) -> list[str]:
    """Keep the components ``profile`` can use, now naming ``new_profile``; return their ids."""
    components = pack / "components"
    if not components.is_dir():
        return []
    kept = []
    for manifest in sorted(components.glob("*.toml")):
        data = tomllib.loads(manifest.read_text("utf-8"))
        if profile not in data.get("compatible_profiles", []):
            manifest.unlink()
            continue
        kept.append(str(data["id"]))
        manifest.write_text(
            re.sub(
                r"(?m)^compatible_profiles = \[.*\]$",
                f'compatible_profiles = ["{new_profile}"]',
                manifest.read_text("utf-8"),
            ),
            "utf-8",
        )
    return kept


def _replace_choices(text: str, question: str, values: list[str]) -> str:
    """Return ``text`` (questions.yml) with ``question``'s choices replaced by ``values``."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line == f"{question}:"), None)
    if start is None:
        raise PackError(f"questions.yml has no {question!r} question.")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith(" ")),
        len(lines),
    )
    block = lines[start:end]
    at = next((i for i, line in enumerate(block) if line.strip().startswith("choices:")), None)
    if at is None:
        raise PackError(f"questions.yml: {question!r} has no choices.")
    stop = at + 1
    while stop < len(block) and block[stop].strip().startswith("- "):
        stop += 1
    indent = block[at][: len(block[at]) - len(block[at].lstrip())]
    replacement = (
        [f"{indent}choices:"] + [f"{indent}  - {value}" for value in values]
        if values
        else [f"{indent}choices: []"]
    )
    block[at:stop] = replacement
    lines[start:end] = block
    return "\n".join(lines) + "\n"


def _rename_references(pack: Path, old: str, new: str) -> None:
    """Rename quoted ``'old'`` profile references in template file names and contents."""
    quoted_old, quoted_new = f"'{old}'", f"'{new}'"
    for path in sorted(pack.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and path.suffix == ".jinja":
            text = path.read_text("utf-8")
            if quoted_old in text:
                path.write_text(text.replace(quoted_old, quoted_new), "utf-8")
        if quoted_old in path.name:
            path.rename(path.with_name(path.name.replace(quoted_old, quoted_new)))
