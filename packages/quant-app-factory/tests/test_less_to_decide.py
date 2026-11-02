"""B-29: a one-command start, only the questions that apply, quiet output, and one way to
name a target (documented examples validate)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_agent_os.runtime.runner import CommandResult
from quant_app_factory import AppFactory, PytemplateUvScaffolder, load_spec
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.spec import Identity

ROOT = Path(__file__).resolve().parents[3]


class _PassingChecks:
    def __call__(self, argv: list[str], *, cwd: Path, timeout: float, **_: Any) -> CommandResult:
        return CommandResult(
            command=argv, cwd=str(cwd), exit_code=0, duration_ms=1, output_bytes=0, stdout_tail=""
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUANT_FACTORY_SETTINGS", str(tmp_path / "factory.toml"))
    monkeypatch.setattr(factory_cli, "git_identity", lambda: Identity())
    factory = AppFactory(PytemplateUvScaffolder(), runner=_PassingChecks())
    monkeypatch.setattr(factory_cli, "default_factory", lambda: factory)
    return tmp_path


# U1: one command


def test_new_with_defaults_and_yes_creates_a_project_without_a_prompt(workspace: Path) -> None:
    result = CliRunner().invoke(
        factory_cli.app, ["new", "quant-research", "my-lab", "--defaults", "-y"]
    )

    assert result.exit_code == 0, result.output
    assert "[y/N]" not in result.output  # nothing was asked
    assert (workspace / "my-lab" / "config.toml").is_file()
    spec = load_spec(workspace / "my-lab.toml")
    assert spec.kind is None and spec.target == ("quant-baseline", "quant-research")
    assert "Created" in result.output


def test_new_with_defaults_asks_once_before_creating(workspace: Path) -> None:
    result = CliRunner().invoke(
        factory_cli.app, ["new", "quant-research", "my-lab", "--defaults"], input="n\n"
    )

    assert result.exit_code == 1
    assert result.output.count("[y/N]") == 1
    assert (workspace / "my-lab.toml").is_file() and not (workspace / "my-lab").exists()


def test_new_with_defaults_needs_a_name(workspace: Path) -> None:
    result = CliRunner().invoke(factory_cli.app, ["new", "quant-research", "--defaults"])

    assert result.exit_code == 1
    assert "Name the project" in result.output


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("quant-research", ("quant-baseline", "quant-research")),
        ("agent-tool", ("agent-tool", "agent-tool")),
        ("python-cli", ("bundled", "python")),
        ("bundled/fastapi", ("bundled", "fastapi")),
        ("./packs/mine", ("./packs/mine", None)),
        (None, (None, None)),
    ],
)
def test_targets_name_a_template_and_profile(
    target: str | None, expected: tuple[str | None, str | None]
) -> None:
    assert factory_cli._resolve_target(target, None, None) == expected


# U2: only the questions that apply


def test_components_are_a_numbered_menu_and_postgres_asks_only_for_its_password(
    workspace: Path,
) -> None:
    # name, description, directory, component 2 (the second optional one), then frequency,
    # max_horizon, cost_bps, db_kind=postgres, host, port, name, user, schema, sslmode, query,
    # read-only; the password variable; do not save; do not create.
    lines = ["pg-lab", "Research", str(workspace), "2", "", "", "", "postgres"]
    lines += ["db.internal", "", "prices", "", "", "", "", "", "PG_PASSWORD", "", "n"]

    result = CliRunner().invoke(
        factory_cli.app, ["new", "quant-research", "-o", "pg.toml"], input="\n".join(lines) + "\n"
    )

    assert result.exit_code == 0, result.output
    assert "1. agent-tools" in result.output and "2. data-database" in result.output
    assert "DATABASE_PASSWORD is secret" in result.output
    assert "DATABASE_URL" not in result.output
    spec = load_spec(workspace / "pg.toml")
    assert spec.components == ["data-database"]
    assert spec.settings == {"DATABASE_PASSWORD": "env:PG_PASSWORD"}
    assert "The app reads DATABASE_PASSWORD from $PG_PASSWORD." in result.output


# U3: quiet by default


def test_create_prints_a_summary_not_every_file(workspace: Path) -> None:
    CliRunner().invoke(factory_cli.app, ["new", "quant-research", "lab", "--defaults", "-y"])
    (workspace / "out").mkdir()
    spec = workspace / "lab.toml"
    text = spec.read_text()
    assert workspace.as_posix() in text  # specs store paths with forward slashes
    spec.write_text(text.replace(workspace.as_posix(), (workspace / "out").as_posix()))

    result = CliRunner().invoke(factory_cli.app, ["create", str(spec), "-y"])

    assert result.exit_code == 0, result.output
    assert "lab/metrics.py" not in result.output
    assert re.search(r"\d+ files; components: data-csv, metrics; checks: uv sync", result.output)


# U4: one way to name a target, and every documented spec validates

_HEREDOC = re.compile(r"<<'TOML'\n(.*?)\nTOML", re.DOTALL)
_TOML_BLOCK = re.compile(r"```toml\n(.*?)```", re.DOTALL)


def _documented_specs() -> list[tuple[str, str]]:
    specs = []
    for path in [
        ROOT / "README.md",
        *(ROOT / "docs").glob("*.md"),
        ROOT / "packages" / "quant-app-factory" / "README.md",
    ]:
        text = path.read_text("utf-8")
        for block in [*_HEREDOC.findall(text), *_TOML_BLOCK.findall(text)]:
            if re.search(r"(?m)^name = ", block):
                specs.append((path.name, block))
    return specs


def test_the_docs_hold_spec_examples() -> None:
    assert len(_documented_specs()) >= 4


@pytest.mark.parametrize(("source", "block"), _documented_specs())
def test_every_documented_spec_validates_and_names_template_and_profile(
    tmp_path: Path, source: str, block: str
) -> None:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(block)

    spec = load_spec(spec_file)

    assert spec.kind is None, f"{source}: use template and profile, not kind"
    assert spec.template and spec.profile
