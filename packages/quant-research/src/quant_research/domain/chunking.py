"""Split documents into passages that keep their line ranges and section headings."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNKER_VERSION = "paragraph-v1"
MAX_CHUNK_CHARS = 1200

_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Passage:
    """Text spanning ``line_start``..``line_end`` (1-based, inclusive) under ``section``."""

    text: str
    line_start: int
    line_end: int
    section: str | None


def split_passages(text: str, *, first_line: int = 1) -> list[Passage]:
    """Pack consecutive paragraphs of one section into passages of at most `MAX_CHUNK_CHARS`.

    ``first_line`` is the line number of ``text``'s first line in the source file, so locations
    stay exact after front matter is removed. A paragraph longer than the limit becomes its own
    passage; passages never cross a heading.
    """
    passages: list[Passage] = []
    for paragraph in _paragraphs(text, first_line):
        previous = passages[-1] if passages else None
        if (
            previous is not None
            and previous.section == paragraph.section
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
