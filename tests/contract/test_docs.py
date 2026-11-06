"""The docs stay true: relative links resolve, and the commands they teach exist."""

import re
import shlex
from pathlib import Path

import pytest
from typer.main import get_command

from quant_platform.cli import app

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = sorted(
    path
    for path in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    if "archive" not in path.parts  # finished plans keep the words of their day
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TAUGHT = ("README.md", "docs/getting-started.md")


def _prose(text: str) -> str:
    """Return ``text`` without fenced code blocks, where brackets are not links."""
    return re.sub(r"(?ms)^ *```.*?^ *```", "", text)


def _anchors(path: Path) -> set[str]:
    headings = re.findall(r"(?m)^#{1,6} +(.+?) *$", _prose(path.read_text("utf-8")))
    slugs = (re.sub(r"[^\w\- ]", "", heading.lower().replace("`", "")) for heading in headings)
    return {slug.replace(" ", "-") for slug in slugs}


def _broken_links(path: Path) -> list[str]:
    broken = []
    for target in LINK.findall(_prose(path.read_text("utf-8"))):
        if re.match(r"[a-z]+:", target):
            continue
        name, _, anchor = target.partition("#")
        resolved = (path.parent / name).resolve() if name else path
        if not resolved.exists() or (
            anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved)
        ):
            broken.append(target)
    return broken


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda path: str(path.relative_to(ROOT)))
def test_relative_links_resolve(path: Path) -> None:
    assert _broken_links(path) == []


def _taught_commands() -> list[str]:
    commands = []
    for name in TAUGHT:
        text = (ROOT / name).read_text("utf-8")
        for block in re.findall(r"(?ms)^```bash\n(.*?)^```", text):
            commands += re.findall(r"(?m)^(?:uv run )?(qaos [^#\n]*?)\s*(?:#.*)?$", block)
    return commands


def test_the_docs_teach_commands() -> None:
    assert len(_taught_commands()) >= 5


@pytest.mark.parametrize("command", _taught_commands())
def test_every_taught_command_and_option_exists(command: str) -> None:
    node = get_command(app)
    words = shlex.split(command)[1:]
    while words and words[0] in getattr(node, "commands", {}):
        node = node.commands[words.pop(0)]  # type: ignore[attr-defined]
    assert not hasattr(node, "commands"), f"{command}: no such command"
    known = {option for param in node.params for option in param.opts + param.secondary_opts}
    for word in words:
        if word.startswith("-"):
            assert word.split("=")[0] in known, f"{command}: no option {word}"
