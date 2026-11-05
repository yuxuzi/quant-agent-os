"""Review fixes for PRs #9 and #10: secrets, TLS, pack drift, retries, and typed answers."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_agent_os.runtime.runner import CommandResult
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory import (
    AppFactory,
    AppSpec,
    Identity,
    PytemplateUvScaffolder,
    ScaffoldRequestError,
    factory_tools,
)
from quant_app_factory import cli as factory_cli
from quant_app_factory.application.factory import redact_values
from quant_app_factory.domain.connections import Connection, apply_connection
from quant_app_factory.domain.planner import BUILT_IN_PACKS
from quant_app_factory.infrastructure.pack_files import fork_pack
from quant_app_factory.tools import CHECK_PERMISSIONS

SECRET = "p@ss word/1"
LOCAL = Connection(kind="sqlite", database="/tmp/prices.db", password_env="DB_PW")


class FakeChecks:
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


def _factory(checks: FakeChecks | None = None, **kwargs: Any) -> AppFactory:
    return AppFactory(
        PytemplateUvScaffolder(),
        runner=checks or FakeChecks(),
        connections=lambda: {"local": LOCAL},
        environ={"DB_PW": SECRET},
        **kwargs,
    )


def _spec(tmp_path: Path, **fields: Any) -> AppSpec:
    base: dict[str, Any] = {
        "name": "db-lab",
        "kind": "quant-research",
        "description": "d",
        "output_dir": str(tmp_path),
        "connection": "local",
    }
    return AppSpec.model_validate(base | fields)


# Secrets


def test_the_connection_check_never_runs_pack_code_the_operator_did_not_review(
    tmp_path: Path,
) -> None:
    pack = fork_pack(
        BUILT_IN_PACKS["quant-baseline"], "quant-research", tmp_path / "pack", pack_id="p"
    )
    checks = FakeChecks()
    out = tmp_path / "out"
    out.mkdir()
    spec = _spec(out, kind=None, template=pack.as_posix(), profile="quant-research")

    report = _factory(checks).create(spec, check_connection=True)

    assert all("--check-connection" not in command for command, _ in checks.calls)
    assert all(SECRET not in extra.values() for _, extra in checks.calls)
    assert any("only for built-in templates" in note for note in report.warnings)


def test_secrets_are_redacted_in_plain_and_url_encoded_forms() -> None:
    text = f"raw {SECRET} quoted p%40ss%20word%2F1 plus p%40ss+word%2F1"

    redacted = redact_values(text, [SECRET])

    assert redacted == "raw [REDACTED] quoted [REDACTED] plus [REDACTED]"


def test_the_check_output_is_redacted_before_it_is_reported(tmp_path: Path) -> None:
    checks = FakeChecks(output="postgresql://u:p%40ss%20word%2F1@db failed")

    report = _factory(checks).create(_spec(tmp_path), check_connection=True)

    assert "p%40ss" not in report.checks[-1].output_tail
    assert checks.calls[-1][1] == {"DATABASE_PASSWORD": SECRET}


# Pack drift


def test_a_pack_changed_after_its_preview_is_refused(tmp_path: Path) -> None:
    pack = fork_pack(
        BUILT_IN_PACKS["quant-baseline"], "quant-research", tmp_path / "pack", pack_id="p"
    )
    out = tmp_path / "out"
    out.mkdir()
    spec = _spec(
        out, kind=None, template=pack.as_posix(), profile="quant-research", connection=None
    )
    factory = _factory()
    reviewed = factory.pack_digest(spec)

    (pack / "template" / "EXTRA.md").write_text("added after review\n")

    with pytest.raises(ScaffoldRequestError, match="changed after it was previewed"):
        factory.create(spec, expected_pack_digest=reviewed)
    assert not (out / "db-lab").exists()
    assert factory.create(spec, expected_pack_digest=factory.pack_digest(spec)).project_path


def test_plan_app_hands_over_the_pack_digest(tmp_path: Path) -> None:
    with Platform(factory_tools(_factory())) as platform:
        built_in = platform.call("plan_app", spec=_spec(tmp_path, connection=None).model_dump())
        bundled = platform.call(
            "plan_app", spec=_spec(tmp_path, kind="python-cli", connection=None).model_dump()
        )

    assert built_in.output is not None and bundled.output is not None
    assert len(built_in.output["create_app_input"]["expected_pack_digest"]) == 64
    assert bundled.output["create_app_input"]["expected_pack_digest"] is None


# Retries


def test_checks_can_be_rerun_on_an_existing_project(tmp_path: Path) -> None:
    checks = FakeChecks()
    factory = _factory(checks)
    spec = _spec(tmp_path)
    factory.create(spec, run_checks=False)

    report = factory.recheck(spec, check_connection=True)

    commands = [command for command, _ in checks.calls]
    assert commands[0] == "uv sync"
    assert commands[-1] == "uv run db-lab check-connection"
    assert report.checks_passed is True
    with pytest.raises(ScaffoldRequestError, match="create it first"):
        factory.recheck(_spec(tmp_path, name="missing-lab"))


def test_check_app_needs_an_approval(tmp_path: Path) -> None:
    factory = _factory()
    spec = _spec(tmp_path)
    factory.create(spec, run_checks=False)
    policy = PermissionPolicy(granted=READ_ONLY | CHECK_PERMISSIONS)
    with Platform(factory_tools(factory), policy=policy) as platform:
        waiting = platform.call("check_app", spec=spec.model_dump(mode="json"))
        platform.approve(waiting.run_id or "", approved_by="op")
        (checked,) = platform.resume(waiting.run_id or "")

    assert waiting.status == "approval_required"
    assert checked.status == "ok"


def test_cli_check_reruns_and_records_the_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = FakeChecks()
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(factory_cli, "default_factory", lambda: _factory(checks))
    spec = _spec(tmp_path, connection=None)
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(
        'name = "db-lab"\nkind = "quant-research"\ndescription = "d"\n'
        f'output_dir = "{tmp_path.as_posix()}"\n'
    )
    _factory().create(spec, run_checks=False)

    result = CliRunner().invoke(factory_cli.app, ["check", str(spec_file), "--yes"])

    assert result.exit_code == 0, result.output
    assert "Checked" in result.output
    assert (tmp_path / "home" / "runs.db").is_file()


# TLS and typed answers


def test_a_saved_connection_carries_its_tls_mode(tmp_path: Path) -> None:
    connection = Connection(kind="postgres", host="db", sslmode="verify-ca")

    spec = apply_connection(_spec(tmp_path, connection=None), connection)

    assert spec.answers["db_sslmode"] == "verify-ca"


def test_postgres_projects_verify_the_certificate_by_default(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path, connection=None, components=["data-database"], answers={"db_kind": "postgres"}
    )

    report = _factory().create(spec, run_checks=False)

    config = (Path(report.project_path) / "db_lab" / "config.py").read_text()
    assert 'DB_SSLMODE: str = "verify-full"' in config


def test_typed_answers_reach_templates_with_their_declared_types(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    files = {
        "pytemplate-pack.toml": (
            'contract = 1\nid = "typed"\nname = "T"\ndescription = "d"\nversion = "1"\n'
        ),
        "copier.yml": (
            '_subdirectory: "{{ profile }}/template"\n_templates_suffix: .jinja\n'
            "--- !include questions.yml\n"
        ),
        "questions.yml": textwrap.dedent(
            """\
            profile:
              type: str
              choices: [note]
            project_name:
              type: str
            package_name:
              type: str
              default: "{{ project_name | replace('-', '_') }}"
            count:
              type: int
              default: 2
            enabled:
              type: bool
              default: false
            components:
              type: str
              multiselect: true
              choices: []
              default: []
            """
        ),
        "note/profile.toml": (
            'contract = 1\nid = "note"\nname = "N"\ndescription = "d"\nversion = "1"\n'
            'kind = "library"\nstatus = "experimental"\nquestions = ["count", "enabled"]\n'
        ),
        "note/template/OUT.md.jinja": "{{ count + 1 }} {{ enabled is sameas true }}\n",
        "note/template/{{ _copier_conf.answers_file }}.jinja": "{{ _copier_answers|to_nice_yaml }}",
    }
    for name, text in files.items():
        (pack / name).parent.mkdir(parents=True, exist_ok=True)
        (pack / name).write_text(text)
    out = tmp_path / "out"
    out.mkdir()
    spec = AppSpec.model_validate(
        {
            "name": "typed-app",
            "template": pack.as_posix(),
            "profile": "note",
            "description": "d",
            "output_dir": str(out),
            "answers": {"count": "5", "enabled": "true"},
        }
    )

    report = AppFactory(PytemplateUvScaffolder(), identity=lambda: Identity()).create(
        spec, run_checks=False
    )

    assert (Path(report.project_path) / "OUT.md").read_text() == "6 True\n"


# Settings updates and connection readiness


def test_concurrent_settings_updates_are_all_kept(tmp_path: Path) -> None:
    import threading

    from quant_app_factory.infrastructure.settings_file import (
        load_settings,
        set_value,
        update_settings,
    )

    path = tmp_path / "factory.toml"

    def add(index: int) -> None:
        update_settings(
            lambda settings: set_value(settings, f"connections.c{index}.kind", "sqlite"), path
        )

    workers = [threading.Thread(target=add, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(load_settings(path).connections) == [f"c{index}" for index in range(8)]
    assert not path.with_name("factory.toml.lock").exists()


def test_a_held_lock_times_out_with_a_clear_message(tmp_path: Path) -> None:
    from quant_app_factory.infrastructure.settings_file import SettingsError, settings_lock

    path = tmp_path / "factory.toml"
    with (
        settings_lock(path),
        pytest.raises(SettingsError, match="another command"),
        settings_lock(path, timeout=0.2),
    ):
        pass


def test_check_spec_reports_a_connection_that_is_not_saved(tmp_path: Path) -> None:
    check = _factory().check_spec(
        {
            "name": "db-lab",
            "kind": "quant-research",
            "description": "d",
            "output_dir": str(tmp_path),
            "connection": "removed-since",
        }
    )

    assert not check.ready
    assert any("removed-since" in problem.message for problem in check.problems)


def test_a_password_connection_needs_no_url(tmp_path: Path) -> None:
    connection = Connection(
        kind="postgres", host="db", database="research", user="analyst", password_env="PG_PW"
    )

    spec = apply_connection(_spec(tmp_path, connection=None), connection)
    report = _factory().create(spec, run_checks=False)

    config = (Path(report.project_path) / "db_lab" / "config.py").read_text()
    database = (Path(report.project_path) / "db_lab" / "database.py").read_text()
    assert spec.settings == {"DATABASE_PASSWORD": "env:PG_PW"}
    assert 'DB_HOST: str = "db"' in config
    assert "PASSWORD_VARIABLE" in database and "URL.create(" in database
