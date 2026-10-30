"""Read Markdown and text files from a corpus directory, with simple front matter."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quant_research.domain.ports import SourceFile

SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})


class TextFileLoader:
    """A `SourceLoader` for UTF-8 Markdown and text files; hidden files are ignored."""

    version = "text-v1"

    def discover(self, root: Path) -> tuple[list[Path], list[Path]]:
        """Return supported and unsupported files under ``root`` (or ``root`` itself), sorted."""
        found = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        paths = [p for p in found if not p.name.startswith(".")]
        supported = [p for p in paths if p.suffix.lower() in SUPPORTED_SUFFIXES]
        return supported, [p for p in paths if p.suffix.lower() not in SUPPORTED_SUFFIXES]

    def load(self, path: Path, root: Path) -> SourceFile:
        """Read ``path`` and split off ``---``-delimited ``key: value`` front matter."""
        return _load(path, root)


def _load(path: Path, root: Path) -> SourceFile:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata: dict[str, str] = {}
    body, first_line = text, 1
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body = "\n".join(lines[index + 1 :])
                first_line = index + 2
                break
            key, _, value = line.partition(":")
            if key.strip() and value.strip():
                metadata[key.strip().lower()] = value.strip().strip("\"'")
        else:
            metadata = {}
    base = root if root.is_dir() else root.parent
    return SourceFile(
        uri=path.relative_to(base).as_posix(),
        sha256=hashlib.sha256(raw).hexdigest(),
        body=body,
        body_first_line=first_line,
        metadata=metadata,
    )
