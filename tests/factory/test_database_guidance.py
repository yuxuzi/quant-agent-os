"""B-25: database guidance: the data-database component, saved connections, and the check."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_agent_os.runtime.runner import CommandResult
from quant_app_factory import AppFactory, AppSpec, Identity, PytemplateUvScaffolder, load_spec
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.connections import Connection, apply_connection
from quant_app_factory.domain.planner import PlanningError
from quant_app_factory.infrastructure.settings_file import load_settings, save_settings, set_value

SECRET = "hunter2-very-secret"
PG = Connection(
    kind="postgres",
    host="db.internal",
    port=5432,
    database="research",
    user="analyst",
    schema="prices",
    password_env="RESEARCH_PG_PASSWORD",
)


class FakeChecks:
    """Record lifecycle commands and the environment each one received."""

    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self, argv: list[str], *, cwd: Path, timeout: float, **kwargs: Any
    ) -> CommandResult:
        self.calls.append((" ".join(argv), dict(kwargs.get("extra_env") or {})))
        return CommandResult(
            command=argv,
            cwd=str(cwd),
            exit_code=0,
            duration_ms=1,
            output_bytes=1,
            stdout_tail=self.output,
        )


def _spec(tmp_path: Path, **fields: Any) -> AppSpec:
    base: dict[str, Any] = {
        "name": "pg-lab",
        "kind": "quant-research",
        "description": "Backtest on a database",
        "output_dir": str(tmp_path),
    }
    return AppSpec.model_validate(base | fields)


def _factory(checks: FakeChecks | None = None, **kwargs: Any) -> AppFactory:
    return AppFactory(
        PytemplateUvScaffolder(),
        runner=checks or FakeChecks(),
        connections=lambda: {"research-pg": PG},
        **kwargs,
    )


def _all_text(project: Path) -> str:
    return "\n".join(
        path.read_text(errors="ignore") for path in project.rglob("*") if path.is_file()
    )


# Connections


def test_a_saved_connection_fills_the_component_answers_and_settings(tmp_path: Path) -> None:
    spec = apply_connection(_spec(tmp_path, answers={"db_user": "override"}), PG)

    assert "data-database" in spec.components
    assert spec.answers["db_kind"] == "postgres"
    assert spec.answers["db_port"] == "5432"
    assert spec.answers["db_user"] == "override"  # the spec wins
    assert spec.settings == {"DATABASE_PASSWORD": "env:RESEARCH_PG_PASSWORD"}


def test_an_unknown_connection_names_the_saved_ones(tmp_path: Path) -> None:
    with pytest.raises(PlanningError, match="research-pg"):
        _factory().plan(_spec(tmp_path, connection="other"))


def test_check_spec_with_a_connection_is_ready_and_binds_the_password(tmp_path: Path) -> None:
    check = _factory().check_spec(
        {
            "name": "pg-lab",
            "kind": "quant-research",
            "description": "d",
            "output_dir": str(tmp_path),
            "connection": "research-pg",
        }
    )

    assert check.ready, check.problems
    assert {"pg_lab/database.py", ".env.example", "tests/test_database.py"} <= set(check.files)
    assert check.unbound_settings == ["DATABASE_URL"]


def test_database_files_appear_only_with_the_database_component(tmp_path: Path) -> None:
    _, preview = _factory().preview(_spec(tmp_path, kind="quant-research"))

    assert not any("database" in name or ".env" in name for name in preview.files)


# Generated files and the connection check


def test_generated_files_never_hold_a_secret_and_the_check_gets_only_its_variable(
    tmp_path: Path,
) -> None:
    checks = FakeChecks(output=f"Connected; password was {SECRET}")
    factory = _factory(checks, environ={"RESEARCH_PG_PASSWORD": SECRET, "HOME": "/x"})

    report = factory.create(_spec(tmp_path, connection="research-pg"), check_connection=True)

    project = Path(report.project_path)
    config = (project / "pg_lab" / "config.py").read_text()
    command, extra = checks.calls[-1]
    assert SECRET not in _all_text(project)
    assert 'DB_HOST: str = "db.internal"' in config
    assert 'DB_SCHEMA: str = "prices"' in config
    assert "DATABASE_PASSWORD=" in (project / ".env.example").read_text()
    assert command == "uv run pg-lab check-connection"
    assert extra == {"DATABASE_PASSWORD": SECRET}
    assert SECRET not in report.checks[-1].output_tail
    assert "[REDACTED]" in report.checks[-1].output_tail


def test_a_missing_variable_is_reported_and_not_invented(tmp_path: Path) -> None:
    checks = FakeChecks()

    report = _factory(checks, environ={}).create(
        _spec(tmp_path, connection="research-pg"), check_connection=True
    )

    assert checks.calls[-1][1] == {}
    assert any("RESEARCH_PG_PASSWORD is not set" in note for note in report.warnings)


def test_there_is_no_connection_to_check_without_the_component(tmp_path: Path) -> None:
    checks = FakeChecks()

    report = _factory(checks).create(_spec(tmp_path), check_connection=True)

    assert all("--check-connection" not in command for command, _ in checks.calls)
    assert any("No database component" in note for note in report.warnings)


# The terminal questionnaire


@pytest.mark.parametrize(
    ("when", "components", "answers", "expected"),
    [
        (None, set(), {}, True),
        ("{{ 'data-database' in components }}", set(), {}, False),
        ("{{ 'data-database' in components }}", {"data-database"}, {}, True),
        (
            "{{ 'data-database' in components and db_kind == 'postgres' }}",
            {"data-database"},
            {"db_kind": "sqlite"},
            False,
        ),
        (
            "{{ 'data-database' in components and db_kind == 'postgres' }}",
            {"data-database"},
            {"db_kind": "postgres"},
            True,
        ),
        ("{{ 'x' not in components }}", {"x"}, {}, False),
        ("{{ something | complicated }}", set(), {}, True),
    ],
)
def test_simple_conditions_decide_which_questions_are_asked(
    when: str | None, components: set[str], answers: dict[str, str], expected: bool
) -> None:
    assert factory_cli._applies(when, components, answers) is expected


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "factory.toml"
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(path.parent))
    monkeypatch.setattr(factory_cli, "git_identity", lambda: Identity())
    return path


def test_new_uses_a_saved_connection_without_asking_its_details(
    tmp_path: Path, settings_file: Path
) -> None:
    settings = load_settings(settings_file)
    settings = settings.model_copy(update={"connections": {"research-pg": PG}})
    save_settings(settings, settings_file)
    out = tmp_path / "pg.toml"
    # name, description, directory, components, connection, then frequency, max_horizon,
    # cost_bps, db_query (defaults kept), then "do not create".
    lines = [
        "pg-lab",
        "Research",
        str(tmp_path),
        "data-database",
        "research-pg",
        "",
        "",
        "",
        "",
        "n",
    ]

    result = CliRunner().invoke(
        factory_cli.app,
        ["new", "--template", "quant-baseline", "-o", str(out)],
        input="\n".join(lines) + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Database host" not in result.output
    spec = load_spec(out)
    assert spec.connection == "research-pg"
    assert not any(name.startswith("db_") for name in spec.answers)


def test_new_asks_for_a_new_sqlite_database_and_saves_it(
    tmp_path: Path, settings_file: Path
) -> None:
    out = tmp_path / "local.toml"
    database = tmp_path / "prices.db"
    sqlite3.connect(database).close()
    # name, description, directory, components; frequency, max_horizon, cost_bps; db_kind
    # (default sqlite), db_name, db_query, db_read_only (defaults); no secret variables for a
    # SQLite file; save as "local"; do not create.
    lines = [
        "local-lab",
        "Research",
        str(tmp_path),
        "data-database",
        "",
        "",
        "",
        "",
        database.as_posix(),
        "",
        "",
        "local",
        "n",
    ]

    result = CliRunner().invoke(
        factory_cli.app,
        ["new", "--template", "quant-baseline", "-o", str(out)],
        input="\n".join(lines) + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Database host" not in result.output  # postgres-only questions are skipped
    assert "DATABASE_PASSWORD" not in result.output and "DATABASE_URL" not in result.output
    assert load_spec(out).answers == {"db_name": database.as_posix()}
    saved = load_settings(settings_file).connections["local"]
    assert (saved.kind, saved.database) == ("sqlite", database.as_posix())


def test_settings_can_describe_a_connection_and_refuse_its_password(tmp_path: Path) -> None:
    settings = set_value(load_settings(tmp_path / "f.toml"), "connections.pg.kind", "postgres")

    settings = set_value(settings, "connections.pg.password_env", "PG_PASSWORD")

    assert settings.connections["pg"].password_env == "PG_PASSWORD"
