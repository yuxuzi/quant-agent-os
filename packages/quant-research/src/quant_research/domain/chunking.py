"""Split documents into passages that keep their line ranges and section headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNKER_VERSION = "paragraph-v2+page-v1"
MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 200
PAGE_CHUNK_CHARS = 500

_SENTENCE_END = (".", "?", "!", ":")

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Passage:
    """Text spanning ``line_start``..``line_end`` (1-based, inclusive) under ``section``."""

    text: str
    line_start: int
    line_end: int
    section: str | None


def split_passages(text: str, *, first_line: int = 1) -> list[Passage]:
    """Return one passage per paragraph, merging short paragraphs into the next one.

    A passage shorter than `MIN_CHUNK_CHARS` absorbs the following paragraph of the same section
    while the result stays within `MAX_CHUNK_CHARS`; longer paragraphs stand alone, so citations
    point at a single paragraph. ``first_line`` is the line number of ``text``'s first line in
    the source file, so locations stay exact after front matter is removed. Passages never cross
    a heading.
    """
    passages: list[Passage] = []
    for paragraph in _paragraphs(text, first_line):
        previous = passages[-1] if passages else None
        if (
            previous is not None
            and previous.section == paragraph.section
            and len(previous.text) < MIN_CHUNK_CHARS
            and len(previous.text) + 2 + len(paragraph.text) <= MAX_CHUNK_CHARS
        ):
            passages[-1] = Passage(
                f"{previous.text}\n\n{paragraph.text}",
                previous.line_start,
                paragraph.line_end,
                previous.section,
            )
        else:
            passages.append(paragraph)
    return passages


def _paragraphs(text: str, first_line: int) -> list[Passage]:
    paragraphs: list[Passage] = []
    section: str | None = None
    lines: list[str] = []
    start = first_line

    def close(end: int) -> None:
        if lines:
            paragraphs.append(Passage("\n".join(lines).strip(), start, end, section))
            lines.clear()

    number = first_line - 1
    for number, line in enumerate(text.splitlines(), start=first_line):
        heading = _HEADING.match(line)
        if heading or not line.strip():
            close(number - 1)
            if heading:
                section = heading.group(1)
            continue
        if not lines:
            start = number
        lines.append(line)
    close(number)
    return paragraphs


def split_page(text: str) -> list[Passage]:
    """Pack the lines of one extracted PDF page into passages of about `PAGE_CHUNK_CHARS`.

    Extracted PDF text rarely keeps blank lines, so paragraphs cannot be detected. A passage
    closes at a line ending a sentence once it holds `PAGE_CHUNK_CHARS` characters, and always
    before exceeding `MAX_CHUNK_CHARS`. Line numbers count lines of the extracted page text.
    """
    passages: list[Passage] = []
    lines: list[str] = []
    start = 1

    def close(end: int) -> None:
        if lines:
            passages.append(Passage("\n".join(lines), start, end, None))
            lines.clear()

    number = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        size = sum(len(item) + 1 for item in lines)
        if lines and size + len(line) > MAX_CHUNK_CHARS:
            close(number - 1)
        if not lines:
            start = number
        lines.append(line)
        if size + len(line) >= PAGE_CHUNK_CHARS and line.endswith(_SENTENCE_END):
            close(number)
    close(number)
    return passages
