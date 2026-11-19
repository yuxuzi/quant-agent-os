"""One call to write a `Document` in the format its file name asks for."""

from __future__ import annotations

import json
from pathlib import Path

from quant_toolbox.report._document import Document, render_html, render_markdown, to_dict

REPORT_FORMATS = (".html", ".md", ".json", ".pdf", ".xlsx")


def write_report(document: Document, path: str | Path, *, charts: str = "static") -> Path:
    """Write ``document`` to ``path`` by its suffix, creating folders, and return the path.

    ``.html`` (``charts`` as in `render_html`), ``.md``, ``.json``, ``.pdf`` (the ``pdf`` extra),
    or ``.xlsx`` (the ``excel`` extra). ``charts`` other than static applies to HTML only.
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in REPORT_FORMATS:
        raise ValueError(f"Write a report as {', '.join(REPORT_FORMATS)}, not {target.name!r}.")
    if charts != "static" and suffix != ".html":
        raise ValueError(f"charts={charts!r} applies to an HTML report, not {suffix}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".pdf":
        from quant_toolbox.report.pdf import write_pdf

        return write_pdf(document, target)
    if suffix == ".xlsx":
        from quant_toolbox.report.excel import write_workbook

        return write_workbook(document, target)
    if suffix == ".html":
        text = render_html(document, charts=charts)
    elif suffix == ".md":
        text = render_markdown(document)
    else:
        text = json.dumps(to_dict(document), indent=2) + "\n"
    target.write_text(text, encoding="utf-8")
    return target
