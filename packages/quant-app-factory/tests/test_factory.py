"""Quant App Factory offline (B-10): planner, pytemplate-uv adapter, checks, tools, and CLI.

Projects are really rendered by the pytemplate-uv library into ``tmp_path``, which needs no
network. Only the generated project's lifecycle commands (`uv sync`, ruff, mypy, pytest) are
replaced by a fake runner; the opt-in smoke test runs them for real.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from pytemplate import api
from typer.testing import CliRunner

from quant_agent_os.runs import SqliteRunStore
from quant_agent_os.runtime.runner import CommandResult
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, PermissionPolicy
from quant_app_factory import (
    AppFactory,
    AppSpec,
    ArchitecturePlanner,
    PlanningError,
    PytemplateUvScaffolder,
    ScaffoldRenderError,
    ScaffoldRequest,
    ScaffoldRequestError,
    SpecError,
    factory_tools,
    load_spec,
)
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.planner import AGENT_TOOL_PACK
from quant_app_factory.tools import CREATE_PERMISSIONS

LIFECYCLE = [
    "uv sync",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy signal_tools tests",
    "uv run pytest",
]


def _spec(tmp_path: Path, **changes: Any) -> AppSpec:
    fields: dict[str, Any] = {
        "name": "signal-tools",
        "kind": "agent-tool",
        "description": "Signal tools for Quant-Agent-OS",
        "output_dir": str(tmp_path),
        "tool_name": "count_signals",
    }
    return AppSpec.model_validate(fields | changes)


class FakeChecks:
    """Stands in for `run_command` on lifecycle commands; ``failing`` commands exit 1."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.failing = failing
        self.calls: list[str] = []

    def __call__(self, argv: list[str], *, cwd: Path, timeout: float, **_: Any) -> CommandResult:
        command = " ".join(argv)
        self.calls.append(command)
        failed = command in self.failing
        return CommandResult(
            command=argv,
            cwd=str(cwd),
            exit_code=1 if failed else 0,
            duration_ms=1,
            output_bytes=0,
            stdout_tail="boom" if failed else "fine",
        )


def _factory(checks: FakeChecks | None = None) -> AppFactory:
    return AppFactory(PytemplateUvScaffolder(), runner=checks or FakeChecks())


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


def test_a_windows_source_path_is_written_with_forward_slashes(tmp_path: Path) -> None:
    spec = _spec(tmp_path, quant_agent_os_source="C:\\work\\quant-agent-os")

    assert spec.quant_agent_os_source == "C:/work/quant-agent-os"


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


# pytemplate-uv adapter (real, in-process rendering)


def test_plan_lists_the_exact_files_and_writes_nothing(tmp_path: Path) -> None:
    _, preview = _factory().preview(_spec(tmp_path))

    assert preview.package_name == "signal_tools"
    assert {"signal_tools/tools.py", "tests/test_tools.py", "pyproject.toml"} <= set(preview.files)
    assert preview.template["pack_id"] == "quant-agent-tool"
    assert preview.lifecycle_commands[-1] == "uv run pytest"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("kind", "components", "expected"),
    [("python-cli", [], ["cli"]), ("fastapi-service", ["database"], ["api", "database"])],
)
def test_bundled_profiles_plan_with_their_components(
    tmp_path: Path, kind: str, components: list[str], expected: list[str]
) -> None:
    spec = _spec(tmp_path, kind=kind, components=components, tool_name=None)

    _, preview = _factory().preview(spec)

    assert sorted(preview.components) == expected
    assert preview.template["kind"] == "bundled"


def test_invalid_requests_become_request_errors(tmp_path: Path) -> None:
    request = ScaffoldRequest(project_name="x", profile="no-such-profile", output_dir=str(tmp_path))

    with pytest.raises(ScaffoldRequestError, match="rejected"):
        PytemplateUvScaffolder().plan(request)


def test_render_failures_become_render_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(request: api.RenderRequest) -> api.RenderResult:
        raise api.RenderError("disk full")

    monkeypatch.setattr(api, "render", fail)
    request = ArchitecturePlanner().plan(_spec(tmp_path)).request

    with pytest.raises(ScaffoldRenderError, match="disk full"):
        PytemplateUvScaffolder().create(request)


# Create and check


def test_create_renders_the_project_then_runs_every_check(tmp_path: Path) -> None:
    checks = FakeChecks()

    report = _factory(checks).create(_spec(tmp_path, quant_agent_os_source=str(tmp_path)))

    project = Path(report.project_path)
    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    assert checks.calls == LIFECYCLE
    assert report.checks_passed is True
    assert report.scaffolder.startswith("pytemplate-uv==3.")
    assert "def count_signals" in (project / "signal_tools" / "tools.py").read_text()
    source = pyproject["tool"]["uv"]["sources"]["quant-agent-os"]
    assert source["path"] == tmp_path.resolve().as_posix()


def test_the_default_source_is_the_git_repository(tmp_path: Path) -> None:
    report = _factory().create(_spec(tmp_path), run_checks=False)

    pyproject = tomllib.loads((Path(report.project_path) / "pyproject.toml").read_text())
    assert pyproject["tool"]["uv"]["sources"]["quant-agent-os"] == {
        "git": "https://github.com/yuxuzi/quant-agent-os"
    }
    assert report.checks_passed is None


def test_a_failed_install_stops_the_checks(tmp_path: Path) -> None:
    report = _factory(FakeChecks(failing=frozenset({"uv sync"}))).create(_spec(tmp_path))

    assert [check.command for check in report.checks] == ["uv sync"]
    assert report.checks_passed is False
    assert "did not run" in report.warnings[0]


def test_only_uv_lifecycle_commands_run(tmp_path: Path) -> None:
    checks, warnings = _factory().check(tmp_path, ["uv run pytest", "rm -rf /"], "pkg")

    assert [check.command for check in checks] == ["uv run pytest"]
    assert "Skipped 'rm -rf /'" in warnings[0]


def test_an_existing_target_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    (tmp_path / "signal-tools").mkdir()
    checks = FakeChecks()

    with pytest.raises(ScaffoldRequestError, match="already exists"):
        _factory(checks).create(_spec(tmp_path))
    assert list((tmp_path / "signal-tools").iterdir()) == []
    assert checks.calls == []


# Tools and approvals (MVP criterion 5)


def test_plan_app_is_read_only_and_previews_the_files(tmp_path: Path) -> None:
    with Platform(factory_tools(_factory())) as platform:
        record = platform.call("plan_app", spec=_spec(tmp_path).model_dump(mode="json"))

    assert record.status == "ok" and record.output is not None
    assert "signal_tools/tools.py" in record.output["preview"]["files"]
    assert record.output["create_app_input"]["spec"]["name"] == "signal-tools"
    assert list(tmp_path.iterdir()) == []


def test_create_app_waits_for_an_approval_then_creates_once(tmp_path: Path) -> None:
    policy = PermissionPolicy(granted=READ_ONLY | CREATE_PERMISSIONS)
    with Platform(factory_tools(_factory()), policy=policy) as platform:
        waiting = platform.call("create_app", spec=_spec(tmp_path).model_dump(mode="json"))
        assert waiting.status == "approval_required"
        assert list(tmp_path.iterdir()) == []

        platform.approve(waiting.run_id or "", approved_by="op")
        (created,) = platform.resume(waiting.run_id or "")
        stored = platform.get_run(waiting.run_id or "")

    assert created.status == "ok" and created.output is not None
    assert created.output["checks_passed"] is True
    assert (tmp_path / "signal-tools" / "pyproject.toml").is_file()
    assert created.approval is not None and not created.approval.startswith("standing")
    assert stored is not None and stored.run.status == "completed"


def test_create_app_is_denied_unless_its_permissions_are_granted(tmp_path: Path) -> None:
    with Platform(factory_tools(_factory())) as platform:
        record = platform.call("create_app", spec=_spec(tmp_path).model_dump(mode="json"))

    assert record.status == "denied"
    assert list(tmp_path.iterdir()) == []


# CLI


@pytest.fixture
def fake_checks(monkeypatch: pytest.MonkeyPatch) -> FakeChecks:
    checks = FakeChecks()
    monkeypatch.setattr(factory_cli, "_factory", lambda: _factory(checks))
    return checks


def _spec_file(tmp_path: Path) -> Path:
    spec_file = tmp_path / "app.toml"
    spec_file.write_text(
        'name = "signal-tools"\nkind = "agent-tool"\ndescription = "Signal tools"\n'
        'tool_name = "count_signals"\noutput_dir = "out"\n'
    )
    (tmp_path / "out").mkdir()
    return spec_file


def test_cli_plan_lists_the_files_and_writes_nothing(
    tmp_path: Path, fake_checks: FakeChecks
) -> None:
    result = CliRunner().invoke(factory_cli.app, ["plan", str(_spec_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "signal_tools/tools.py" in result.output
    assert list((tmp_path / "out").iterdir()) == []


def test_cli_create_records_the_operator_approval(tmp_path: Path, fake_checks: FakeChecks) -> None:
    runs = tmp_path / "runs.db"

    result = CliRunner().invoke(
        factory_cli.app, ["create", str(_spec_file(tmp_path)), "--yes", "--runs", str(runs)]
    )

    assert result.exit_code == 0, result.output
    assert "ok uv run pytest" in " ".join(result.output.split())
    assert (tmp_path / "out" / "signal-tools" / "pyproject.toml").is_file()
    with SqliteRunStore(runs) as store:
        (run,) = store.list_runs()
        stored = store.get_run(run.run_id)
    assert stored is not None
    granted = [event for event in stored.events if event.event == "approval.granted"]
    assert granted and granted[0].details["approved_by"].startswith("cli:")


def test_cli_create_stops_when_the_operator_declines(
    tmp_path: Path, fake_checks: FakeChecks
) -> None:
    result = CliRunner().invoke(
        factory_cli.app,
        ["create", str(_spec_file(tmp_path)), "--runs", str(tmp_path / "r.db")],
        input="n\n",
    )

    assert result.exit_code == 1
    assert list((tmp_path / "out").iterdir()) == []
    assert fake_checks.calls == []
