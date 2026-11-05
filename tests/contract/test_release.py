"""Release consistency: the one wheel, the plugin, and the changelog agree on a version."""

import json
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict[str, Any]:
    project: dict[str, Any] = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    return project


def test_the_wheel_holds_all_five_import_names() -> None:
    backend = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["uv"][
        "build-backend"
    ]

    assert set(backend["module-name"]) == {path.name for path in (ROOT / "src").iterdir()} - {
        "__pycache__"
    }


def test_the_base_install_stays_light_for_generated_projects() -> None:
    assert sorted(item.split(">")[0] for item in _project()["dependencies"]) == [
        "pydantic",
        "pydantic-settings",
        "rich",
        "typer",
    ]


def test_the_plugin_and_changelog_name_that_version() -> None:
    version = _project()["version"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")

    assert plugin["version"] == version
    assert f"## [{version}]" in changelog or "## [Unreleased]" in changelog


def test_the_release_workflow_builds_and_checks_the_wheel() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

    assert "uv build\n" in workflow
    assert "tests/contract/test_release.py" in workflow
    assert "gh release create" in workflow
    assert "if: vars.PUBLISH_TO_PYPI == 'true'" in workflow  # PyPI is opt-in
