"""B-23: quant baseline templates and user packs, rendered offline by pytemplate-uv.

The generated projects' own checks (uv sync, ruff, mypy, pytest with the bias tests) run in the
opt-in smoke test; here the templates are rendered and inspected.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from quant_app_factory import (
    AppFactory,
    AppSpec,
    ArchitecturePlanner,
    PlanningError,
    PytemplateUvScaffolder,
    ScaffoldRequestError,
    SpecError,
    load_spec,
)
from quant_app_factory.domain.planner import QUANT_BASELINE_PACK


def _spec(tmp_path: Path, **fields: Any) -> AppSpec:
    base: dict[str, Any] = {
        "name": "momo-lab",
        "description": "Momentum research",
        "output_dir": str(tmp_path),
    }
    return AppSpec.model_validate(base | fields)


def _files(tmp_path: Path, **fields: Any) -> set[str]:
    _, preview = AppFactory(PytemplateUvScaffolder()).preview(_spec(tmp_path, **fields))
    return set(preview.files)


# Spec: template, profile, answers


def test_a_spec_names_its_template_and_profile(tmp_path: Path) -> None:
    spec = _spec(tmp_path, template="quant-baseline", profile="quant-lab")

    assert spec.target == ("quant-baseline", "quant-lab")


@pytest.mark.parametrize("old", ["quant-research", "alpha-research", "backtest", "report"])
def test_the_old_profile_names_are_refused_with_the_one_on_offer(tmp_path: Path, old: str) -> None:
    spec = _spec(tmp_path, template="quant-baseline", profile=old)

    with pytest.raises(PlanningError, match="it offers quant-lab"):
        AppFactory(PytemplateUvScaffolder()).preview(spec)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({}, "template"),
        ({"template": "quant-baseline"}, "profile"),
        ({"kind": "quant-research"}, "kind was removed; set template and profile"),
        ({"template": "relative/pack", "profile": "x"}, "absolute pack directory"),
        (
            {"template": "quant-baseline", "profile": "quant-lab", "answers": {"author": "x"}},
            "as a spec field",
        ),
        (
            {"template": "quant-baseline", "profile": "quant-lab", "answers": {"Bad": "x"}},
            "lowercase identifier",
        ),
        (
            {"template": "quant-baseline", "profile": "quant-lab", "tool_name": "x"},
            "only to the agent-tool profile",
        ),
    ],
)
def test_invalid_targets_and_answers_are_explained(
    tmp_path: Path, fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _spec(tmp_path, **fields)


def test_a_relative_template_path_is_relative_to_the_spec_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(
        'name = "x"\ndescription = "d"\ntemplate = "packs/mine"\nprofile = "note"\n'
    )

    assert load_spec(spec_file).template == (tmp_path / "packs" / "mine").resolve().as_posix()


def test_an_unknown_built_in_profile_lists_the_offered_ones(tmp_path: Path) -> None:
    spec = _spec(tmp_path, template="quant-baseline", profile="options")

    with pytest.raises(PlanningError, match="offers quant-lab"):
        ArchitecturePlanner().plan(spec)


@pytest.mark.parametrize(
    ("fields", "portable"),
    [
        ({"template": "quant-baseline", "profile": "quant-lab"}, True),
        (
            {"template": "quant-baseline", "profile": "quant-lab", "components": ["agent-tools"]},
            False,
        ),
        ({"template": "agent-tool", "profile": "agent-tool"}, False),
        ({"template": "bundled", "profile": "python"}, True),
    ],
)
def test_portability_follows_the_platform_dependency(
    tmp_path: Path, fields: dict[str, Any], portable: bool
) -> None:
    assert ArchitecturePlanner().plan(_spec(tmp_path, **fields)).portable is portable


# The quant-baseline pack


def test_one_project_holds_the_study_backtest_and_report(tmp_path: Path) -> None:
    files = _files(tmp_path, template="quant-baseline", profile="quant-lab")

    assert {
        "config.toml",
        "LIMITATIONS.md",
        "momo_lab/metrics.py",
        "momo_lab/parameters.py",
        "momo_lab/study.py",
        "momo_lab/backtest.py",
        "momo_lab/report.py",
        "tests/test_timing.py",
        "tests/test_parameters.py",
    } <= files
    assert not {"momo_lab/tools.py", "momo_lab/database.py"} & files


def test_optional_components_add_their_files_and_dependencies(tmp_path: Path) -> None:
    _, preview = AppFactory(PytemplateUvScaffolder()).preview(
        _spec(
            tmp_path,
            template="quant-baseline",
            profile="quant-lab",
            components=["data-parquet", "agent-tools"],
        )
    )

    assert {"momo_lab/tools.py", "tests/test_tools.py"} <= set(preview.files)
    assert "pyarrow>=15" in preview.dependencies
    assert "quant-agent-os" in preview.dependencies
    assert sorted(preview.components) == ["agent-tools", "data-csv", "data-parquet", "metrics"]


def test_answers_reach_the_generated_code(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        template="quant-baseline",
        profile="quant-lab",
        answers={"frequency": "weekly", "cost_bps": "2.5"},
    )

    report = AppFactory(PytemplateUvScaffolder()).create(spec, run_checks=False)

    config = (Path(report.project_path) / "momo_lab" / "config.py").read_text()
    assert "PERIODS_PER_YEAR: int = 52" in config
    assert "COST_BPS: float = 2.5" in config
    assert report.template_name == "quant-baseline"


def test_an_answer_the_profile_does_not_ask_is_rejected(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path, template="quant-baseline", profile="quant-lab", answers={"db_kind": "duckdb"}
    )

    with pytest.raises(ScaffoldRequestError, match="db_kind"):
        AppFactory(PytemplateUvScaffolder()).preview(spec)


def test_the_pack_is_a_valid_pytemplate_pack() -> None:
    assert (QUANT_BASELINE_PACK / "pytemplate-pack.toml").is_file()
    profiles = sorted(path.parent.name for path in QUANT_BASELINE_PACK.glob("*/profile.toml"))
    assert profiles == ["quant-lab"]


# User packs


def _user_pack(root: Path) -> Path:
    files = {
        "pytemplate-pack.toml": """
            contract = 1
            id = "my-notes"
            name = "My notes"
            description = "A pack that asks only for a title."
            version = "1.0.0"
        """,
        "copier.yml": """
            _subdirectory: "{{ profile }}/template"
            _templates_suffix: .jinja
            --- !include questions.yml
        """,
        "questions.yml": """
            profile:
              type: str
              choices: [note]
            project_name:
              type: str
            package_name:
              type: str
              default: "{{ project_name | replace('-', '_') }}"
            title:
              type: str
              default: Notes
            components:
              type: str
              multiselect: true
              choices: []
              default: []
        """,
        "note/profile.toml": """
            contract = 1
            id = "note"
            name = "Note"
            description = "A single README."
            version = "1"
            kind = "library"
            status = "experimental"
            questions = ["title"]
        """,
        "note/template/README.md.jinja": "# {{ title }}\n",
        "note/template/{{ _copier_conf.answers_file }}.jinja": "{{ _copier_answers|to_nice_yaml }}",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())
    return root


def test_a_user_pack_renders_and_ignores_identity_it_does_not_ask(tmp_path: Path) -> None:
    pack = _user_pack(tmp_path / "pack")
    out = tmp_path / "out"
    out.mkdir()
    spec = _spec(out, template=pack.as_posix(), profile="note", answers={"title": "Ideas"})

    report = AppFactory(PytemplateUvScaffolder()).create(spec, run_checks=False)

    assert (Path(report.project_path) / "README.md").read_text() == "# Ideas\n"
    assert ArchitecturePlanner().plan(spec).portable is None


def test_a_user_pack_still_rejects_answers_it_does_not_ask(tmp_path: Path) -> None:
    pack = _user_pack(tmp_path / "pack")
    spec = _spec(tmp_path, template=pack.as_posix(), profile="note", answers={"colour": "red"})

    with pytest.raises(ScaffoldRequestError, match="colour"):
        AppFactory(PytemplateUvScaffolder()).preview(spec)


def test_a_missing_user_pack_is_a_request_error(tmp_path: Path) -> None:
    spec = _spec(tmp_path, template=(tmp_path / "nope").as_posix(), profile="note")

    with pytest.raises(ScaffoldRequestError, match="does not exist"):
        AppFactory(PytemplateUvScaffolder()).preview(spec)


def test_spec_errors_name_the_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text('name = "x"\ndescription = "d"\n')

    with pytest.raises(SpecError, match=r"app\.toml"):
        load_spec(spec_file)
