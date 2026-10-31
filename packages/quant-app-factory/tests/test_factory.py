"""Quant App Factory offline (B-10): planner, pytemplate-uv adapter, checks, tools, and CLI.

pytemplate-uv is replaced by its recorded JSON output (fixtures/pytemplate-uv-3.0), returned by
a fake subprocess runner, so these tests need no network and never write a real project.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.runtime.runner import CommandResult
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory import (
    PYTEMPLATE_UV,
    AppFactory,
    AppSpec,
    ArchitecturePlanner,
    PlanningError,
    PytemplateUvScaffolder,
    ScaffoldError,
    ScaffoldRenderError,
    ScaffoldRequestError,
    ScaffoldUnavailableError,
    SpecError,
    factory_tools,
    load_spec,
)
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.planner import AGENT_TOOL_PACK
from quant_app_factory.infrastructure.pytemplate_uv import render_request
from quant_app_factory.tools import CREATE_PERMISSIONS

FIXTURES = Path(__file__).parent / "fixtures" / "pytemplate-uv-3.0"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def _spec(tmp_path: Path, **changes: Any) -> AppSpec:
    fields: dict[str, Any] = {
        "name": "signal-tools",
        "kind": "agent-tool",
        "description": "Signal tools for Quant-Agent-OS",
        "output_dir": str(tmp_path),
        "tool_name": "count_signals",
    }
    return AppSpec.model_validate(fields | changes)


class FakeRunner:
    """Answers pytemplate-uv commands with recorded JSON and lifecycle commands with exit codes."""

    def __init__(
        self,
        responses: dict[str, tuple[int | None, str]] | None = None,
        failing: frozenset[str] = frozenset(),
    ) -> None:
        self.responses = responses or {
            "plan": (0, _fixture("agent-tool-plan.json")),
            "create-from-config": (0, _fixture("agent-tool-create.json")),
        }
        self.failing = failing
        self.calls: list[list[str]] = []
        self.requests: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], *, cwd: Path, timeout: float, **_: Any) -> CommandResult:
        self.calls.append(list(argv))
        if argv[:2] == ["uvx", PYTEMPLATE_UV]:
            self.requests.append(tomllib.loads(Path(argv[3]).read_text("utf-8")))
            exit_code, stdout = self.responses[argv[2]]
            return CommandResult(
                command=argv,
                cwd=str(cwd),
                exit_code=exit_code,
                duration_ms=1,
                output_bytes=0,
                stdout_tail=stdout,
                stderr_tail="" if exit_code is not None else "not found",
            )
        failed = " ".join(argv) in self.failing
        return CommandResult(
            command=argv,
            cwd=str(cwd),
            exit_code=1 if failed else 0,
            duration_ms=1,
            output_bytes=0,
            stdout_tail="boom" if failed else "fine",
        )


def _factory(runner: FakeRunner) -> AppFactory:
    return AppFactory(PytemplateUvScaffolder(runner=runner), runner=runner)


# Spec and planner


def test_specs_load_from_toml_with_paths_relative_to_the_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(
        'name = "pricing-cli"\nkind = "python-cli"\ndescription = "Prices things"\n'
        'output_dir = "projects"\n'
    )

    spec = load_spec(spec_file)

    assert spec.output_dir == str((tmp_path / "projects").resolve())
    assert spec.package_name == "pricing_cli"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "Bad_Name"},
        {"description": 'has "quotes"'},
        {"output_dir": "relative/dir"},
        {"kind": "python-cli", "tool_name": "x"},
    ],
)
def test_invalid_specs_are_rejected(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _spec(tmp_path, **changes)


def test_an_unreadable_spec_file_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match=r"app\.toml"):
        load_spec(tmp_path / "app.toml")


def test_agent_tools_use_the_factory_pack_and_depend_on_the_platform(tmp_path: Path) -> None:
    plan = ArchitecturePlanner().plan(_spec(tmp_path, quant_agent_os_source=str(tmp_path)))

    assert plan.request.profile == "agent-tool"
    assert plan.request.template == AGENT_TOOL_PACK.as_posix()
    assert (AGENT_TOOL_PACK / "pytemplate-pack.toml").is_file()
    assert plan.request.data["tool_name"] == "count_signals"
    assert plan.request.data["quant_agent_os_source"] == tmp_path.resolve().as_posix()
    assert plan.portable is False


@pytest.mark.parametrize(
    ("kind", "profile"), [("python-cli", "python"), ("fastapi-service", "fastapi")]
)
def test_other_kinds_use_bundled_profiles_and_stay_portable(
    tmp_path: Path, kind: str, profile: str
) -> None:
    plan = ArchitecturePlanner().plan(_spec(tmp_path, kind=kind, tool_name=None))

    assert (plan.request.profile, plan.request.template, plan.portable) == (profile, None, True)
    assert "tool_name" not in plan.request.data


@pytest.mark.parametrize(
    ("kind", "components", "message"),
    [
        ("agent-tool", ["database"], "offers none"),
        ("python-cli", ["database"], "offers container"),
        ("fastapi-service", ["auth"], "needs a mode"),
        ("fastapi-service", ["auth", "auth-api-key", "auth-oidc"], "one authentication mode"),
    ],
)
def test_the_planner_explains_impossible_requests(
    tmp_path: Path, kind: str, components: list[str], message: str
) -> None:
    tool = "count_signals" if kind == "agent-tool" else None
    spec = _spec(tmp_path, kind=kind, components=components, tool_name=tool)

    with pytest.raises(PlanningError, match=message):
        ArchitecturePlanner().plan(spec)


# pytemplate-uv adapter


def test_the_request_file_follows_pytemplate_uvs_request_schema(tmp_path: Path) -> None:
    request = ArchitecturePlanner().plan(_spec(tmp_path)).request

    parsed = tomllib.loads(render_request(request))

    assert parsed == {
        "project_name": "signal-tools",
        "profile": "agent-tool",
        "output_dir": Path(request.output_dir).as_posix(),
        "components": [],
        "no_input": True,
        "template": request.template,
        "data": request.data,
    }


def test_plan_runs_the_pinned_cli_and_parses_its_json(tmp_path: Path) -> None:
    runner = FakeRunner()

    _, preview = _factory(runner).preview(_spec(tmp_path))

    command = runner.calls[0]
    assert command[:3] == ["uvx", "pytemplate-uv==3.0.*", "plan"] and command[-1] == "--json"
    assert runner.requests[0]["profile"] == "agent-tool"
    assert preview.package_name == "signal_tools"
    assert "signal_tools/tools.py" in preview.files
    assert "tests/test_tools.py" in preview.files
    assert preview.template["pack_id"] == "quant-agent-tool"


@pytest.mark.parametrize(
    ("exit_code", "stdout", "error"),
    [
        (1, _fixture("error-exists.json"), ScaffoldRequestError),
        (
            2,
            '{"ok": false, "exit_code": 2, "error": {"message": "disk full"}}',
            ScaffoldRenderError,
        ),
        (None, "", ScaffoldUnavailableError),
        (0, "not json", ScaffoldError),
    ],
)
def test_pytemplate_uv_failures_become_typed_errors(
    tmp_path: Path, exit_code: int | None, stdout: str, error: type[Exception]
) -> None:
    runner = FakeRunner({"plan": (exit_code, stdout)})

    with pytest.raises(error):
        _factory(runner).preview(_spec(tmp_path))


# Create and check


def test_create_scaffolds_then_runs_every_check_with_the_package_name(tmp_path: Path) -> None:
    runner = FakeRunner()

    report = _factory(runner).create(_spec(tmp_path))

    lifecycle = [" ".join(call) for call in runner.calls[2:]]
    assert lifecycle == [
        "uv sync",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy signal_tools tests",
        "uv run pytest",
    ]
    assert report.checks_passed is True
    assert report.scaffolder == PYTEMPLATE_UV
    assert report.project_path.endswith("signal-tools")


def test_a_failed_install_stops_the_checks(tmp_path: Path) -> None:
    runner = FakeRunner(failing=frozenset({"uv sync"}))

    report = _factory(runner).create(_spec(tmp_path))

    assert [check.command for check in report.checks] == ["uv sync"]
    assert report.checks_passed is False
    assert "did not run" in report.warnings[0]


def test_only_uv_lifecycle_commands_run(tmp_path: Path) -> None:
    factory = _factory(FakeRunner())

    checks, warnings = factory.check(tmp_path, ["uv run pytest", "rm -rf /"], "pkg")

    assert [check.command for check in checks] == ["uv run pytest"]
    assert "Skipped 'rm -rf /'" in warnings[0]


def test_an_existing_target_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    plan = json.loads(_fixture("agent-tool-plan.json")) | {"target_exists": True}
    runner = FakeRunner({"plan": (0, json.dumps(plan))})

    with pytest.raises(ScaffoldRequestError, match="already exists"):
        _factory(runner).create(_spec(tmp_path))
    assert len(runner.calls) == 1


# Tools and approvals (MVP criterion 5)


def test_plan_app_is_read_only_and_hands_over_the_input_to_approve(tmp_path: Path) -> None:
    runner = FakeRunner()
    with Platform(factory_tools(_factory(runner))) as platform:
        record = platform.call("plan_app", spec=_spec(tmp_path).model_dump(mode="json"))

    assert record.status == "ok" and record.output is not None
    assert record.output["create_app_input"]["spec"]["name"] == "signal-tools"
    assert runner.calls == []


def test_create_app_waits_for_an_approval_then_creates_once(tmp_path: Path) -> None:
    runner = FakeRunner()
    policy = PermissionPolicy(granted=READ_ONLY | CREATE_PERMISSIONS)
    with Platform(factory_tools(_factory(runner)), policy=policy) as platform:
        waiting = platform.call("create_app", spec=_spec(tmp_path).model_dump(mode="json"))
        assert waiting.status == "approval_required"
        assert runner.calls == []

        platform.approve(waiting.run_id or "", approved_by="op")
        (created,) = platform.resume(waiting.run_id or "")
        stored = platform.get_run(waiting.run_id or "")

    assert created.status == "ok" and created.output is not None
    assert created.output["checks_passed"] is True
    assert created.approval is not None and not created.approval.startswith("standing")
    assert stored is not None and stored.run.status == "completed"


def test_create_app_is_denied_unless_its_permissions_are_granted(tmp_path: Path) -> None:
    with Platform(factory_tools(_factory(FakeRunner()))) as platform:
        record = platform.call("create_app", spec=_spec(tmp_path).model_dump(mode="json"))

    assert record.status == "denied"


# CLI


@pytest.fixture
def fake_cli(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    runner = FakeRunner()
    monkeypatch.setattr(factory_cli, "_factory", lambda: _factory(runner))
    return runner


def _spec_file(tmp_path: Path) -> Path:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(
        'name = "signal-tools"\nkind = "agent-tool"\ndescription = "Signal tools"\n'
        'tool_name = "count_signals"\n'
    )
    return spec_file


def test_cli_plan_lists_the_files(tmp_path: Path, fake_cli: FakeRunner) -> None:
    result = CliRunner().invoke(factory_cli.app, ["plan", str(_spec_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "signal_tools/tools.py" in result.output
    assert len(fake_cli.calls) == 1


def test_cli_create_records_the_operator_approval(tmp_path: Path, fake_cli: FakeRunner) -> None:
    runs = tmp_path / "runs.db"

    result = CliRunner().invoke(
        factory_cli.app, ["create", str(_spec_file(tmp_path)), "--yes", "--runs", str(runs)]
    )

    assert result.exit_code == 0, result.output
    assert "ok uv run pytest" in " ".join(result.output.split())
    with SqliteRunStore(runs) as store:
        (run,) = store.list_runs()
        stored = store.get_run(run.run_id)
    assert stored is not None
    granted = [event for event in stored.events if event.event == "approval.granted"]
    assert granted and granted[0].details["approved_by"].startswith("cli:")


def test_cli_create_stops_when_the_operator_declines(tmp_path: Path, fake_cli: FakeRunner) -> None:
    result = CliRunner().invoke(
        factory_cli.app,
        ["create", str(_spec_file(tmp_path)), "--runs", str(tmp_path / "r.db")],
        input="n\n",
    )

    assert result.exit_code == 1
    assert [call[2] for call in fake_cli.calls] == ["plan"]
