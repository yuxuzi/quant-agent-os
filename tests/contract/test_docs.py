"""The docs stay true: relative links resolve, and the commands they teach exist."""

import re
import shlex
from pathlib import Path

import pytest
import yaml
from typer.main import get_command

from quant_platform.cli import app

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = sorted(
    path
    for path in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    if "archive" not in path.parts  # finished plans keep the words of their day
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TAUGHT = ("README.md", "docs/getting-started.md", "docs/deployment.md", "docs/operations.md")
COMPOSE_FILES = ("deploy/compose.yaml", "deploy/compose.postgres.yaml")


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


def _assert_qaos_command_exists(command: str) -> None:
    node = get_command(app)
    words = shlex.split(command)[1:]
    while words and words[0] in getattr(node, "commands", {}):
        node = node.commands[words.pop(0)]  # type: ignore[attr-defined]
    if default := getattr(node, "default_command", None):
        node = node.commands[default]  # type: ignore[attr-defined]
    assert not hasattr(node, "commands"), f"{command}: no such command"
    known = {option for param in node.params for option in param.opts + param.secondary_opts}
    for word in words:
        if word.startswith("-"):
            assert word.split("=")[0] in known, f"{command}: no option {word}"


@pytest.mark.parametrize("command", _taught_commands())
def test_every_taught_command_and_option_exists(command: str) -> None:
    _assert_qaos_command_exists(command)


def _taught_compose_commands() -> list[str]:
    """Return each `docker compose` line in the docs and in the compose files' own comments."""
    texts = [(ROOT / name).read_text("utf-8") for name in TAUGHT]
    texts += [re.sub(r"(?m)^# *", "", (ROOT / name).read_text("utf-8")) for name in COMPOSE_FILES]
    commands: list[str] = []
    for text in texts:
        joined = text.replace("\\\n", " ")
        commands += re.findall(r"(?m)^(?:\w+=\S+ )*(docker compose -f .*)$", joined)
    return commands


def _compose_parts(command: str) -> tuple[str, str, str | None, list[str]]:
    """Split a `docker compose` line into its file, subcommand, service, and arguments."""
    words = shlex.split(command, comments=True)
    for index, word in enumerate(words):
        if word in {">", "<", "|", "&&"}:
            words = words[:index]
            break
    file, words = words[3], words[4:]
    while words[0].startswith("-"):  # `--env-file x` and the like, before the subcommand
        words = words[2:]
    subcommand, words = words[0], words[1:]
    while words and words[0].startswith("-"):  # `--rm`, `-T`, `-d`: none takes a value
        words = words[1:]
    return file, subcommand, words[0] if words else None, words[1:]


def test_the_docs_teach_compose_commands() -> None:
    assert len(_taught_compose_commands()) >= 10


@pytest.mark.parametrize("command", _taught_compose_commands())
def test_every_taught_compose_command_exists(command: str) -> None:
    """A `run` line is the service's own command plus its arguments, so check them together."""
    file, subcommand, service, arguments = _compose_parts(command)
    services = yaml.safe_load((ROOT / file).read_text("utf-8"))["services"]
    if service is None:
        return
    assert service in services, f"{command}: {file} has no service {service}"
    entrypoint = services[service].get("entrypoint", [])
    if subcommand == "run" and entrypoint[:1] == ["qaos"]:
        _assert_qaos_command_exists(shlex.join([*entrypoint, *arguments]))


def test_a_wrong_compose_subcommand_is_caught() -> None:
    """The bug this guards: the guide taught `datasets push`, and that command is `data add`."""
    file, _, service, arguments = _compose_parts(
        "docker compose -f deploy/compose.yaml run --rm datasets push /import/a.csv --id a"
    )
    entrypoint = yaml.safe_load((ROOT / file).read_text("utf-8"))["services"][service]["entrypoint"]

    with pytest.raises(AssertionError, match="no such command"):
        _assert_qaos_command_exists(shlex.join([*entrypoint, *arguments]))
