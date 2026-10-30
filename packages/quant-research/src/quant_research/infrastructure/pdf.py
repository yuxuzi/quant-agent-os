"""PDF loading with page-level locations (optional extra ``quant-agent-os-research[pdf]``).

Text is extracted page by page with pypdf, so chunk locations name the page and the line range
within that page's extracted text. Scanned PDFs have no text layer; they load with empty pages
and ingestion warns, because OCR is out of scope.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from quant_research.domain.ports import SourceFile, SourceReadError
from quant_research.infrastructure.files import uri_for

VERSION = "pdf-v1"
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
            pages = () if encrypted else tuple(page.extract_text() or "" for page in reader.pages)
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
