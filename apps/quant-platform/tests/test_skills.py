"""The skills (plan step P3.2) agree with what the platform server actually serves.

Skills are instructions only. Every tool, prompt, resource, and field a skill names must exist in
the composed catalog, every served tool, prompt, and resource template must be taught by some
skill, and no skill may need a newer server than this one.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, get_args

import pytest

from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.runtime.executor import CallStatus
from quant_agent_os.tools import PermissionPolicy
from quant_app_factory import AppFactory, PytemplateUvScaffolder
from quant_platform import Catalog, compose
from quant_research import open_research

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "skills"
# Frontmatter fields every Agent Skills client accepts (Claude Code reads more).
PORTABLE_FIELDS = {"name", "description", "license", "compatibility", "metadata"}
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
BACKTICKED = re.compile(r"`([^`\n]+)`")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")  # snake_case with an underscore
SKILL_NAME = re.compile(r"^quant-[a-z-]+$")


@dataclass(frozen=True)
class Skill:
    """One SKILL.md: its directory name, frontmatter, and body."""

    directory: str
    fields: dict[str, Any]
    body: str

    def mentions(self) -> set[str]:
        """Return every backticked token in the body."""
        return set(BACKTICKED.findall(self.body))


def parse(path: Path) -> Skill:
    """Read a SKILL.md: YAML-style frontmatter of scalars and one ``metadata`` mapping."""
    text = path.read_text("utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    fields: dict[str, Any] = {}
    nested: dict[str, str] | None = None
    for line in match.group(1).splitlines():
        if line.startswith("  ") and nested is not None:
            key, _, value = line.strip().partition(":")
            nested[key] = value.strip().strip('"')
            continue
        key, _, value = line.partition(":")
        if value.strip():
            fields[key] = value.strip()
            nested = None
        else:
            nested = fields[key] = {}
    return Skill(path.parent.name, fields, match.group(2))


def load_skills() -> list[Skill]:
    """Return every skill under ``skills/``, by name."""
    return [parse(path) for path in sorted(SKILLS.glob("*/SKILL.md"))]


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Catalog]:
    """Everything the server offers with all three toolsets."""
    with open_research(":memory:") as research:
        yield compose(
            policy=PermissionPolicy(),
            research=research.research,
            datasets=DirectoryDatasetStore(tmp_path_factory.mktemp("datasets")),
            factory=AppFactory(PytemplateUvScaffolder()),
        )


def _field_names(schema: Any) -> set[str]:
    """Return every property name anywhere in a JSON schema."""
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _field_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= _field_names(item)
    return names


def _known(catalog: Catalog) -> set[str]:
    names = {spec.name for spec in catalog.tools} | {prompt.name for prompt in catalog.prompts}
    names |= set(get_args(CallStatus))  # statuses a failed call reports
    for spec in catalog.tools:
        names |= _field_names(spec.input_model.model_json_schema())
        names |= _field_names(spec.output_model.model_json_schema())
    return names


def test_there_is_one_skill_per_workflow() -> None:
    assert [skill.directory for skill in load_skills()] == [
        "quant-analytics",
        "quant-app-factory",
        "quant-research",
        "quant-strategy",
    ]


@pytest.mark.parametrize("skill", load_skills(), ids=lambda skill: skill.directory)
def test_each_skill_is_a_portable_instruction_only_skill(skill: Skill) -> None:
    fields = skill.fields
    assert set(fields) <= PORTABLE_FIELDS
    assert fields["name"] == skill.directory and NAME.match(fields["name"])
    assert 0 < len(fields["description"]) <= 1024
    assert "Use when" in fields["description"]  # it says when to load it
    assert len(fields.get("compatibility", "")) <= 500
    assert fields["license"] == "MIT"
    files = [path.name for path in (SKILLS / skill.directory).rglob("*") if path.is_file()]
    assert files == ["SKILL.md"]  # no scripts: every action goes through the server's tools


@pytest.mark.parametrize("skill", load_skills(), ids=lambda skill: skill.directory)
def test_no_skill_needs_a_newer_server(skill: Skill) -> None:
    def parts(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))

    needed = skill.fields["metadata"]["min-server-version"]
    assert parts(needed) <= parts(version("quant-agent-os-platform"))


@pytest.mark.parametrize("skill", load_skills(), ids=lambda skill: skill.directory)
def test_every_name_a_skill_mentions_exists(skill: Skill, catalog: Catalog) -> None:
    known = _known(catalog)
    templates = {resource.uri_template for resource in catalog.resources}
    skills = {other.directory for other in load_skills()}
    mentions = skill.mentions()

    unknown = sorted(token for token in mentions if IDENTIFIER.match(token) and token not in known)
    resources = sorted(token for token in mentions if token.startswith("qaos://"))
    others = sorted(token for token in mentions if SKILL_NAME.match(token))

    assert unknown == [], f"{skill.directory} names tools or fields that do not exist"
    assert set(resources) <= templates, f"{skill.directory} names unknown resources"
    assert set(others) <= skills, f"{skill.directory} refers to skills that do not exist"


def test_every_served_tool_prompt_and_resource_is_taught(catalog: Catalog) -> None:
    taught = set().union(*(skill.mentions() for skill in load_skills()))

    assert sorted({spec.name for spec in catalog.tools} - taught) == []
    assert sorted({prompt.name for prompt in catalog.prompts} - taught) == []
    assert sorted({resource.uri_template for resource in catalog.resources} - taught) == []
