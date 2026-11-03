"""Release consistency: every package ships at one version and pins its siblings to it.

The platform's `serverInfo.version` is the core version and `qaos://server/info` reports the
platform version (decision 0015), so the two must never differ in an installation.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = [
    ROOT / "pyproject.toml",
    *sorted(ROOT.glob("packages/*/pyproject.toml")),
    *sorted(ROOT.glob("apps/*/pyproject.toml")),
]
SIBLING = re.compile(r"^(quant-agent-os(?:-[a-z]+)?)(\[[a-z,]+\])?(.*)$")


def _projects() -> dict[str, dict[str, Any]]:
    return {
        path.parent.name: tomllib.loads(path.read_text("utf-8"))["project"] for path in PROJECTS
    }


def test_every_package_has_the_same_version() -> None:
    versions = {name: project["version"] for name, project in _projects().items()}

    assert len(set(versions.values())) == 1, versions


def test_siblings_are_pinned_to_that_version() -> None:
    projects = _projects()
    version = projects[ROOT.name]["version"]
    names = {project["name"] for project in projects.values()}
    for project in projects.values():
        extras = project.get("optional-dependencies", {}).values()
        requirements = [
            *project.get("dependencies", []),
            *(item for extra in extras for item in extra),
        ]
        for requirement in requirements:
            found = SIBLING.match(requirement)
            if found and found.group(1) in names:
                assert found.group(3) == f"=={version}", f"{project['name']}: {requirement}"


def test_the_plugin_and_changelog_name_that_version() -> None:
    version = _projects()[ROOT.name]["version"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")

    assert plugin["version"] == version
    assert f"## [{version}]" in changelog or "## [Unreleased]" in changelog


def test_the_release_workflow_builds_and_checks_every_package() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

    assert "uv build --all-packages" in workflow
    assert "tests/contract/test_release.py" in workflow
    assert "gh release create" in workflow
    assert "if: vars.PUBLISH_TO_PYPI == 'true'" in workflow  # PyPI is opt-in
