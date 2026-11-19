"""Release consistency: the wheels, the plugin, and the changelogs agree on their versions."""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TOOLBOX = ROOT / "packages" / "quant-toolbox"


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


def _toolbox() -> dict[str, Any]:
    project: dict[str, Any] = tomllib.loads((TOOLBOX / "pyproject.toml").read_text("utf-8"))[
        "project"
    ]
    return project


def test_the_platform_pins_the_toolbox_version_it_is_released_with() -> None:
    version = _toolbox()["version"]
    pins = {
        extra: requirement
        for extra, requirements in _project()["optional-dependencies"].items()
        for requirement in requirements
        if requirement.startswith("quant-toolbox")
    }

    assert set(pins) == {"toolbox", "interactive", "excel", "empyrical", "sql"}
    assert all(requirement.endswith(f"=={version}") for requirement in pins.values()), pins


def test_the_toolbox_needs_only_numpy_and_names_its_version_in_its_changelog() -> None:
    project = _toolbox()
    changelog = (TOOLBOX / "CHANGELOG.md").read_text("utf-8")

    assert [re.split(r"[<>=]", item)[0] for item in project["dependencies"]] == ["numpy"]
    assert project["license"] == "MIT"
    assert f"## [{project['version']}]" in changelog or "## [Unreleased]" in changelog
