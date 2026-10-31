"""PDF loading with page-level locations (optional extra ``quant-agent-os-research[pdf]``).

Text is extracted page by page with pypdf, so chunk locations name the page and the line range
within that page's extracted text. Scanned PDFs have no text layer; they load with empty pages
and ingestion warns, because OCR is out of scope.

Extracted text is normalised (`normalize_pages`) without moving any line, so locations still
point at the PDF's own lines: ligatures become letters ("ﬁ" to "fi"), and a word hyphenated
across a line break is rejoined on the first line ("momen-" + "tum" to "momentum").
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from quant_research.domain.ports import SourceFile, SourceReadError
from quant_research.infrastructure.files import uri_for

VERSION = "pdf-v2"
INSTALL_HINT = "install the PDF extra: uv add 'quant-agent-os-research[pdf]'"


class PdfLoader:
    """A `SourceLoader` for PDF files, backed by pypdf."""

    version = VERSION
    suffixes = frozenset({".pdf"})

    def __init__(self) -> None:
        try:
            import pypdf  # noqa: F401 - checked here so a missing extra fails at wiring time
        except ImportError as error:
            raise SourceReadError(f"PDF support is not installed; {INSTALL_HINT}.") from error

    def load(self, path: Path, root: Path) -> SourceFile:
        """Extract each page's text and the document metadata."""
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        raw = path.read_bytes()
        pypdf_logger = logging.getLogger("pypdf")
        level = pypdf_logger.level
        pypdf_logger.setLevel(logging.ERROR)  # repair notices would duplicate our warning
        try:
            reader = PdfReader(path)
            encrypted = reader.is_encrypted
            pages = (
                ()
                if encrypted
                else normalize_pages([page.extract_text() or "" for page in reader.pages])
            )
            info = None if encrypted else reader.metadata
        except (PdfReadError, ValueError, KeyError, OSError) as error:
            raise SourceReadError(f"{path.name} is not a readable PDF ({error}).") from error
        finally:
            pypdf_logger.setLevel(level)
        if encrypted:
            raise SourceReadError(f"{path.name} is encrypted; supply an unencrypted copy.")
        metadata = {
            key: value
            for key, value in {
                "title": (info.title if info else None) or "",
                "authors": (info.author if info else None) or "",
            }.items()
            if value
        }
        return SourceFile(
            uri=uri_for(path, root),
            sha256=hashlib.sha256(raw).hexdigest(),
            body="",
            body_first_line=1,
            metadata=metadata,
            pages=pages,
            loader_version=VERSION,
        )


_LINE_BREAK_HYPHEN = re.compile(r"([A-Za-z]+)-$")
_CONTINUATION = re.compile(r"^([a-z]+)(\S*)\s*")
_WORDS = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def normalize_pages(pages: Sequence[str]) -> tuple[str, ...]:
    """Return ``pages`` with ligatures expanded and line-break hyphenation undone.

    A word split as "momen-" and "tum" on the next line becomes "momentum" on the first line,
    and the next line keeps the rest of its text, so every page keeps its line count. The
    hyphen stays when it belongs to the word: when the document spells the word with a hyphen
    more often than without ("cross-sectional"), or when the first part is a single letter
    ("t-statistic").
    """
    expanded = [unicodedata.normalize("NFKC", page) for page in pages]
    spellings = Counter(word.lower() for page in expanded for word in _WORDS.findall(page))
    return tuple(_rejoin(page, spellings) for page in expanded)


def _rejoin(page: str, spellings: Counter[str]) -> str:
    lines = [line.rstrip() for line in page.splitlines()]
    for index in range(len(lines) - 1):
        head = _LINE_BREAK_HYPHEN.search(lines[index])
        tail = _CONTINUATION.match(lines[index + 1].lstrip())
        if head is None or tail is None:
            continue
        first, rest = head.group(1), tail.group(1)
        hyphenated = spellings[f"{first}-{rest}".lower()]
        keep = len(first) == 1 or hyphenated > spellings[(first + rest).lower()]
        joined = f"{first}-{rest}" if keep else first + rest
        lines[index] = lines[index][: head.start()] + joined + tail.group(2)
        lines[index + 1] = lines[index + 1].lstrip()[tail.end() :]
    return "\n".join(lines)


def extract_pages(path: Path) -> tuple[str, ...]:
    """Return the normalised text of every page of the PDF at ``path`` (as ingestion sees it)."""
    from pypdf import PdfReader

    return normalize_pages([page.extract_text() or "" for page in PdfReader(path).pages])


class UnavailableLoader:
    """Claims a suffix whose loader is not installed and explains how to enable it."""

    version = "unavailable"

    def __init__(self, suffixes: frozenset[str], reason: str) -> None:
        self.suffixes = suffixes
        self._reason = reason

    def load(self, path: Path, root: Path) -> SourceFile:
        """Always raise `SourceReadError` with the installation hint."""
        raise SourceReadError(f"Skipped {path.name}: {self._reason}")


def pdf_loader() -> PdfLoader | UnavailableLoader:
    """Return a `PdfLoader`, or a placeholder that explains how to install PDF support."""
    try:
        return PdfLoader()
    except SourceReadError:
        return UnavailableLoader(
            frozenset({".pdf"}), f"PDF support is not installed; {INSTALL_HINT}."
        )
