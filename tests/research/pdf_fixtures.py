"""Generate small, real PDFs for tests, so no binary fixtures are committed.

`write_pdf` emits a minimal PDF 1.4 file (Helvetica, one text object per page, a correct xref
table) that pypdf extracts line by line. An empty string in a page's lines is a blank line, so
paragraphs survive extraction.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(
    path: Path, pages: list[list[str]], *, title: str | None = None, author: str | None = None
) -> Path:
    """Write ``pages`` (each a list of lines) to ``path`` as a text-layer PDF."""
    objects: list[bytes] = []
    page_ids = [4 + 2 * index for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page_id, lines in zip(page_ids, pages, strict=True):
        body = (
            "BT /F1 11 Tf 14 TL 72 740 Td\n"
            + "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
            + "\nET"
        )
        stream = body.encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    info = []
    if title:
        info.append(f"/Title ({_escape(title)})")
    if author:
        info.append(f"/Author ({_escape(author)})")
    objects.append(f"<< {' '.join(info)} >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


TEXT_CORPUS = Path(__file__).parent / "fixtures" / "corpus"

LOW_VOLATILITY_PAGES = [
    [
        "Low-volatility anomaly in US equities",
        "Betting against beta correlates with quality minus junk.",
        "The low-volatility anomaly is tested on US equities.",
        "We sort stocks on trailing beta every month and hold the portfolios for one month.",
        "Portfolios are formed with decile sorts on CRSP data.",
        "Low beta stocks earn higher risk-adjusted returns than the CAPM implies, and the",
        "spread between the lowest and highest beta deciles is large and persistent.",
        "Leverage constraints are the leading explanation: investors who cannot borrow buy",
        "high beta stocks instead, which bids up their prices and lowers their returns.",
    ],
    [
        "Results",
        "Low beta portfolios are partly explained by profitability.",
        "Carry is tested on futures markets.",
        "Trend following relies on time-series momentum in futures markets.",
        "Factor crowding has reduced the premium since 2010.",
    ],
]


def build_corpus(destination: Path) -> Path:
    """Copy the text corpus to ``destination`` and add generated PDFs under ``papers/``.

    The PDFs are a two-page synthetic paper, a "scanned" PDF with no text layer, and a corrupt
    file with a ``.pdf`` suffix.
    """
    shutil.copytree(TEXT_CORPUS, destination, dirs_exist_ok=True)
    papers = destination / "papers"
    write_pdf(
        papers / "low-volatility.pdf",
        LOW_VOLATILITY_PAGES,
        title="Low-volatility anomaly in US equities",
        author="Synthetic Author D",
    )
    write_pdf(papers / "scanned.pdf", [[]])
    (papers / "broken.pdf").write_bytes(b"%PDF-1.4\nthis is not really a pdf\n")
    return destination
