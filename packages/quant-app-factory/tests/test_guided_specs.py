"""B-24: guided specs (describe_template, check_spec, quant-factory new) and factory settings."""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from quant_agent_os.runtime.runner import CommandResult
from quant_agent_os.sdk import Platform
from quant_app_factory import (
    AppFactory,
    AppSpec,
    Identity,
    PytemplateUvScaffolder,
    dump_spec,
    guide_tools,
    load_spec,
)
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.secrets import secret_problem
from quant_app_factory.infrastructure.settings_file import (
    SettingsError,
    git_identity,
    load_settings,
    remove_value,
    save_settings,
    set_value,
)


def _factory(**kwargs: Any) -> AppFactory:
    return AppFactory(PytemplateUvScaffolder(), **kwargs)


def _raw(tmp_path: Path, **fields: Any) -> dict[str, Any]:
    return {
        "name": "momo-lab",
        "kind": "backtest",
        "description": "Momentum research",
        "output_dir": str(tmp_path),
    } | fields


# Secrets


@pytest.mark.parametrize(
    ("name", "value", "secret", "refused"),
    [
        ("DATABASE_URL", "env:DATABASE_URL", True, False),
        ("DATABASE_URL", "postgresql://db/research", True, True),
        ("API_TOKEN", "abc123", False, True),
        ("db_password", "hunter2", False, True),
        ("SOURCE", "postgresql://user:hunter2@db/research", False, True),
        ("HOST", "db.internal", False, False),
    ],
)
def test_secrets_are_refused_unless_they_are_references(
    name: str, value: str, secret: bool, refused: bool
) -> None:
    assert (secret_problem(name, value, declared_secret=secret) is not None) is refused


def test_a_spec_refuses_secret_values_in_answers_and_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="env:API_TOKEN"):
        AppSpec.model_validate(_raw(tmp_path, settings={"API_TOKEN": "abc"}))
    with pytest.raises(ValueError, match="credentials in a URL"):
        AppSpec.model_validate(_raw(tmp_path, answers={"source": "pg://u:p@h/db"}))

    assert AppSpec.model_validate(_raw(tmp_path, settings={"API_TOKEN": "env:TOKEN"}))


def test_a_spec_round_trips_through_its_toml(tmp_path: Path) -> None:
    spec = AppSpec.model_validate(
        _raw(tmp_path, answers={"cost_bps": "2.5"}, components=["data-parquet"])
    )
    path = tmp_path / "spec.toml"
    path.write_text(dump_spec(spec))

    assert load_spec(path) == spec
    assert "author" not in path.read_text()  # defaults stay out of the file


# describe_template


def test_describing_a_profile_lists_only_what_it_can_use() -> None:
    described = _factory().describe_template("quant-baseline", "backtest")

    names = {question.name for question in described.questions}
    assert {"frequency", "cost_bps"} <= names
    assert "max_horizon" not in names
    assert {c.id for c in described.components} == {
        "agent-tools",
        "data-csv",
        "data-parquet",
        "metrics",
    }


def test_describing_marks_secret_settings() -> None:
    described = _factory().describe_template("bundled", "fastapi")

    database = next(c for c in described.components if c.id == "database")
    assert [(s.name, s.secret) for s in database.settings] == [("DATABASE_URL", True)]


# check_spec


def test_a_ready_spec_lists_its_files_and_toml(tmp_path: Path) -> None:
    check = _factory().check_spec(_raw(tmp_path, answers={"cost_bps": "3"}))

    assert check.ready, check.problems
    assert "momo_lab/backtest.py" in check.files
    assert check.spec_toml and 'cost_bps = "3"' in check.spec_toml


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({"description": None}, "description", "string"),
        ({"components": ["database"]}, "template", "offers"),
        ({"kind": "report", "answers": {"cost_bps": "5"}}, "template", "cost_bps"),
        ({"settings": {"WHATEVER": "1"}}, "settings.WHATEVER", "no selected component"),
    ],
)
def test_check_spec_reports_each_kind_of_problem(
    tmp_path: Path, fields: dict[str, Any], field: str, message: str
) -> None:
    check = _factory().check_spec(_raw(tmp_path, **fields))

    assert not check.ready
    assert any(p.field == field and message in p.message for p in check.problems), check.problems


def test_a_declared_secret_setting_needs_a_reference(tmp_path: Path) -> None:
    raw = _raw(
        tmp_path,
        kind="fastapi-service",
        components=["database"],
        settings={"DATABASE_URL": "postgresql+asyncpg://db/research"},
    )

    check = _factory().check_spec(raw)
    fixed = _factory().check_spec(raw | {"settings": {"DATABASE_URL": "env:RESEARCH_DB"}})

    assert any("env:DATABASE_URL" in p.message for p in check.problems)
    assert fixed.ready, fixed.problems
    assert "DATABASE_URL" not in fixed.unbound_settings


def test_an_existing_target_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "momo-lab").mkdir()

    check = _factory().check_spec(_raw(tmp_path))

    assert [p.field for p in check.problems] == ["name"]


def test_the_guiding_tools_are_read_only_and_answer_through_the_platform(tmp_path: Path) -> None:
    with Platform(guide_tools(_factory())) as platform:
        described = platform.call("describe_template", template="quant-baseline")
        checked = platform.call("check_spec", spec=_raw(tmp_path))

    assert described.status == checked.status == "ok"
    assert checked.output is not None and checked.output["check"]["ready"] is True
    assert "quant-factory create" in checked.output["next_step"]
    assert list(tmp_path.iterdir()) == []


# Identity


def test_identity_fills_only_what_the_spec_leaves_out(tmp_path: Path) -> None:
    factory = _factory(identity=lambda: Identity(author="Ada", email="ada@example.com"))

    filled = factory.plan(AppSpec.model_validate(_raw(tmp_path))).request.defaults
    kept = factory.plan(AppSpec.model_validate(_raw(tmp_path, author="Grace"))).request.defaults

    assert (filled["author"], filled["email"]) == ("Ada", "ada@example.com")
    assert kept["author"] == "Grace"
    assert "license" not in filled  # the template's own default applies


def test_git_identity_reads_git_config_and_survives_without_git() -> None:
    def fake(argv: list[str], **_: Any) -> CommandResult:
        values = {"user.name": "Ada Lovelace", "user.email": "ada@example.com"}
        return CommandResult(
            command=argv,
            cwd=".",
            exit_code=0,
            duration_ms=1,
            output_bytes=1,
            stdout_tail=values[argv[-1]] + "\n",
        )

    def missing(argv: list[str], **_: Any) -> CommandResult:
        return CommandResult(command=argv, cwd=".", exit_code=None, duration_ms=1, output_bytes=0)

    assert git_identity(fake) == Identity(author="Ada Lovelace", email="ada@example.com")
    assert git_identity(missing) == Identity()


# The settings file


def test_settings_round_trip_with_connections_as_variable_names(tmp_path: Path) -> None:
    path = tmp_path / "factory.toml"
    settings = load_settings(path)
    for key, value in [
        ("identity.author", "Ada"),
        ("connections.research-pg.kind", "postgres"),
        ("connections.research-pg.port", "5432"),
        ("connections.research-pg.schema", "prices"),
        ("connections.research-pg.password_env", "RESEARCH_PG_PASSWORD"),
    ]:
        settings = set_value(settings, key, value)
    save_settings(settings, path)

    loaded = load_settings(path)
    data = tomllib.loads(path.read_text())
    assert loaded.connections["research-pg"].port == 5432
    assert data["connections"]["research-pg"]["schema"] == "prices"
    assert loaded.connections["research-pg"].read_only is True
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("connections.pg.password", "hunter2", "secret"),
        ("connections.pg.database", "pg://u:p@h/db", "credentials in a URL"),
        ("connections.pg.password_env", "not a name", "environment-variable name"),
        ("somewhere.else", "x", "Unknown setting"),
    ],
)
def test_settings_refuse_secrets_and_unknown_keys(key: str, value: str, message: str) -> None:
    settings = set_value(
        load_settings(Path("/nonexistent/factory.toml")), "connections.pg.kind", "sqlite"
    )

    with pytest.raises(SettingsError, match=message):
        set_value(settings, key, value)


def test_removing_a_connection_and_a_broken_file(tmp_path: Path) -> None:
    settings = set_value(load_settings(tmp_path / "none.toml"), "connections.pg.kind", "duckdb")
    broken = tmp_path / "factory.toml"
    broken.write_text("[identity]\npassword = 'x'\n")

    assert remove_value(settings, "connections.pg").connections == {}
    with pytest.raises(SettingsError, match=r"factory\.toml"):
        load_settings(broken)


# CLI


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "factory.toml"
    monkeypatch.setenv("QUANT_FACTORY_SETTINGS", str(path))
    monkeypatch.setattr(factory_cli, "git_identity", lambda: Identity())
    return path


def test_new_asks_until_the_spec_is_ready_and_writes_it(
    tmp_path: Path, settings_file: Path
) -> None:
    out = tmp_path / "momo.toml"
    answers = "\n".join(["momo-lab", "Momentum research", str(tmp_path), "", "weekly", "2.5", "n"])

    result = CliRunner().invoke(
        factory_cli.app,
        ["new", "--template", "quant-baseline", "--profile", "backtest", "-o", str(out)],
        input=answers + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Ready:" in result.output
    spec = load_spec(out)
    assert spec.target == ("quant-baseline", "backtest")
    assert spec.answers == {"frequency": "weekly", "cost_bps": "2.5"}
    assert not (tmp_path / "momo-lab").exists()


def test_new_asks_for_secret_settings_as_variable_names(
    tmp_path: Path, settings_file: Path
) -> None:
    out = tmp_path / "svc.toml"
    # name, description, directory, components, then the fastapi profile's questions
    # (blank keeps defaults), then settings (blank leaves them to runtime, except the secret).
    lines = ["svc", "A service", str(tmp_path), "database"] + [""] * 40
    result = CliRunner().invoke(
        factory_cli.app,
        ["new", "--template", "bundled", "--profile", "fastapi", "-o", str(out)],
        input="\n".join(lines) + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "DATABASE_URL is secret" in result.output
    assert "DATABASE_URL" in result.output


def test_settings_commands_never_store_secrets(settings_file: Path) -> None:
    runner = CliRunner()

    assert (
        runner.invoke(factory_cli.app, ["settings", "set", "identity.author", "Ada"]).exit_code == 0
    )
    refused = runner.invoke(
        factory_cli.app, ["settings", "set", "connections.pg.password", "hunter2"]
    )
    shown = runner.invoke(factory_cli.app, ["settings", "show"])

    assert refused.exit_code == 1
    assert 'author = "Ada"' in shown.output
    assert "hunter2" not in settings_file.read_text()


def test_serve_mcp_offers_the_guide_but_not_create_app(tmp_path: Path) -> None:
    env = dict(os.environ) | {"QUANT_FACTORY_SETTINGS": str(tmp_path / "factory.toml")}
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "quant_app_factory.cli", "serve-mcp", "--no-record"],
        env=env,
    )

    async def main() -> Any:
        with open(os.devnull, "w") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialised = await session.initialize()
                tools = [tool.name for tool in (await session.list_tools()).tools]
                checked = await session.call_tool("check_spec", {"spec": _raw(tmp_path)})
                return initialised, tools, checked

    initialised, tools, checked = anyio.run(main)

    assert initialised.serverInfo.name == "quant-factory"
    assert sorted(tools) == ["check_spec", "describe_template", "plan_app"]
    assert checked.structuredContent["check"]["ready"] is True
