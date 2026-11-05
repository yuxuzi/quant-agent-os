"""Read Markdown and text files from a corpus directory, with simple front matter."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quant_research.domain.ports import SourceFile, SourceReadError

VERSION = "text-v1"


class TextFileLoader:
    """A `SourceLoader` for UTF-8 Markdown and text files with optional front matter."""

    version = VERSION
    suffixes = frozenset({".md", ".markdown", ".txt"})

    def load(self, path: Path, root: Path) -> SourceFile:
        """Read ``path`` and split off ``---``-delimited ``key: value`` front matter."""
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceReadError(
                f"{path.name} is not valid UTF-8; re-save it as UTF-8."
            ) from error
        return _parse(text, raw, uri_for(path, root))


def uri_for(path: Path, root: Path) -> str:
    """Return ``path`` relative to the corpus root, with forward slashes."""
    base = root if root.is_dir() else root.parent
    return path.relative_to(base).as_posix()


def _parse(text: str, raw: bytes, uri: str) -> SourceFile:
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
    return SourceFile(
        uri=uri,
        sha256=hashlib.sha256(raw).hexdigest(),
        body=body,
        body_first_line=first_line,
        metadata=metadata,
        loader_version=VERSION,
    )
