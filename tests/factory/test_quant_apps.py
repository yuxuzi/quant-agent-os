"""The quant-apps pack: one customizable application per stage of the investment process.

Each application is rendered offline and its own tests run in this environment. Its lint, type
checks, and locked install run in the opt-in smoke test, and in `test_factory_smoke`.
"""

from __future__ import annotations

import calendar
import filecmp
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quant_app_factory import AppFactory, AppSpec, PlanningError, PytemplateUvScaffolder
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.planner import BUILT_IN_PACKS, QUANT_APP_PROFILES, QUANT_APPS_PACK

PACKAGE = "{{ package_name }}"


def _spec(tmp_path: Path, profile: str, **fields: Any) -> AppSpec:
    return AppSpec.model_validate(
        {
            "name": "fund-app",
            "template": "quant-apps",
            "profile": profile,
            "description": "A fund application",
            "output_dir": str(tmp_path),
            **fields,
        }
    )


def _folder(path: Path) -> Path:
    path.mkdir()
    return path


def _render(tmp_path: Path, profile: str, **fields: Any) -> Path:
    spec = _spec(tmp_path, profile, **fields)
    report = AppFactory(PytemplateUvScaffolder()).create(spec, run_checks=False)
    return Path(report.project_path)


def _run_python(
    project: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(project)}
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd or project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _app(project: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    code = "from fund_app.cli import app; app()"
    return _run_python(project, "-c", code, *args, cwd=cwd)


# The catalogue


def test_the_pack_offers_one_application_per_stage() -> None:
    profiles = sorted(path.parent.name for path in QUANT_APPS_PACK.glob("*/profile.toml"))

    assert profiles == sorted(QUANT_APP_PROFILES)
    assert BUILT_IN_PACKS["quant-apps"] == QUANT_APPS_PACK


@pytest.mark.parametrize("profile", QUANT_APP_PROFILES)
def test_every_application_can_be_customized_and_explains_how(tmp_path: Path, profile: str) -> None:
    _, preview = AppFactory(PytemplateUvScaffolder()).preview(_spec(tmp_path, profile))
    files = set(preview.files)

    assert {"config.toml", "AGENTS.md", "README.md", "LIMITATIONS.md"} <= files
    assert {"fund_app/cli.py", "fund_app/firm/loaders.py"} <= files
    assert "fund_app/tools.py" not in files


@pytest.mark.parametrize("profile", QUANT_APP_PROFILES)
def test_agent_tools_are_opt_in(tmp_path: Path, profile: str) -> None:
    spec = _spec(tmp_path, profile, components=["agent-tools"])

    plan, preview = AppFactory(PytemplateUvScaffolder()).preview(spec)

    assert {"fund_app/tools.py", "tests/test_tools.py"} <= set(preview.files)
    assert plan.portable is False


def test_a_component_the_application_does_not_offer_is_refused(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "risk-monitor", components=["data-database"])

    with pytest.raises(PlanningError, match="offers agent-tools"):
        AppFactory(PytemplateUvScaffolder()).preview(spec)


@pytest.mark.parametrize("profile", QUANT_APP_PROFILES)
def test_the_new_command_knows_each_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    done = CliRunner().invoke(
        factory_cli.app, ["new", profile, "my-app", "--defaults"], input="n\n"
    )

    assert (tmp_path / "my-app.toml").is_file(), done.output
    assert f'profile = "{profile}"' in (tmp_path / "my-app.toml").read_text()


def test_a_long_description_wraps_and_the_project_still_lints(tmp_path: Path) -> None:
    """The description lands in the package docstring; 101 characters once broke E501."""
    description = (
        "Quarterly tear sheets for the equity long/short book and its benchmarks, "
        "for the PM and risk reviews."
    )
    assert len(description) == 101

    project = _render(tmp_path, "performance-report", description=description)

    init = (project / "fund_app" / "__init__.py").read_text("utf-8")
    assert max(len(line) for line in init.splitlines()) <= 100
    for command in (["check", "."], ["format", "--check", "."]):
        done = subprocess.run(
            [sys.executable, "-m", "ruff", *command],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr


def test_source_names_where_the_project_installs_quant_agent_os_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    checkout = _folder(tmp_path / "quant-agent-os")
    mirror = "git+https://mirror.example.com/fund/quant-agent-os@v9.9.9"

    local = CliRunner().invoke(
        factory_cli.app,
        ["new", "performance-report", "local-app", "--defaults", "--source", str(checkout)],
        input="n\n",
    )
    remote = CliRunner().invoke(
        factory_cli.app,
        ["new", "performance-report", "mirror-app", "--defaults", "--source", mirror],
        input="n\n",
    )

    assert (
        f'quant_agent_os_source = "{checkout.as_posix()}"'
        in (tmp_path / "local-app.toml").read_text()
    ), local.output
    assert f'quant_agent_os_source = "{mirror}"' in (tmp_path / "mirror-app.toml").read_text(), (
        remote.output
    )


def test_source_is_refused_where_the_template_installs_no_quant_agent_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    done = CliRunner().invoke(
        factory_cli.app,
        ["new", "bundled/python", "plain-lib", "--defaults", "--source", str(tmp_path)],
        input="n\n",
    )

    assert done.exit_code == 1
    assert "--source does not apply" in done.output
    assert not (tmp_path / "plain-lib.toml").exists()


def test_a_monthly_file_gets_its_periods_from_its_dates_by_default(tmp_path: Path) -> None:
    """The round-1 finding: --defaults annualised monthly returns as daily, with no warning."""
    pytest.importorskip("openpyxl")
    project = _render(_folder(tmp_path / "app"), "performance-report")
    work = _folder(tmp_path / "work")
    rows = ["date,Fund"]
    for year in (2023, 2024):
        for month in range(1, 13):
            last = calendar.monthrange(year, month)[1]
            rows.append(f"{year}-{month:02d}-{last},0.01")
    returns = work / "monthly.csv"
    returns.write_text("\n".join(rows) + "\n", "utf-8")

    done = _app(project, "report", "--returns", str(returns), cwd=work)

    assert done.returncode == 0, done.stderr
    written = next(work.glob("reports/*/report.md")).read_text()
    assert "12 periods a year (monthly, inferred from the dates)" in written
    assert "12.68%" in written  # 1.01 ** 12 - 1, the ITD of 1% a month over two years

    (work / "config.toml").write_text("periods_per_year = 252\n", "utf-8")
    refused = _app(project, "report", "--returns", str(returns), cwd=work)
    assert refused.returncode == 1
    assert "periods_per_year is 252" in refused.stderr


def test_the_periods_question_writes_auto_or_the_number_answered(tmp_path: Path) -> None:
    default = _render(_folder(tmp_path / "auto"), "performance-report")
    quarterly = _render(
        _folder(tmp_path / "quarterly"), "performance-report", answers={"periods_per_year": "4"}
    )

    assert 'periods_per_year = "auto"' in (default / "config.toml").read_text()
    assert "periods_per_year = 4\n" in (quarterly / "config.toml").read_text()


# One copy of the numbers: Quant Toolbox (decision 0026)

PACKS = [QUANT_APPS_PACK, BUILT_IN_PACKS["quant-baseline"]]
LIBRARY_MODULES = (
    "metrics.py",
    "risk_metrics.py",
    "tables.py",
    "document.py",
    "horizons.py",
    "excel.py",
    "evaluation.py",
    "report.py",
    "synthetic.py",
)


@pytest.mark.parametrize(
    "pack", [QUANT_APPS_PACK, BUILT_IN_PACKS["quant-baseline"]], ids=["apps", "baseline"]
)
def test_no_application_carries_its_own_copy_of_the_library(pack: Path) -> None:
    copies = sorted(
        path.relative_to(pack).as_posix()
        for name in LIBRARY_MODULES
        for path in pack.glob(f"**/template/{PACKAGE}/{name}")
    )

    assert copies == [], "These belong in Quant Toolbox, imported from quant_toolbox"


@pytest.mark.parametrize(
    "pack", [QUANT_APPS_PACK, BUILT_IN_PACKS["quant-baseline"]], ids=["apps", "baseline"]
)
def test_applications_import_only_the_toolbox_public_names(pack: Path) -> None:
    private = sorted(
        f"{path.relative_to(pack).as_posix()}: {line.strip()}"
        for path in [*pack.rglob("*.py"), *pack.rglob("*.py.jinja")]
        for line in path.read_text("utf-8").splitlines()
        if "quant_toolbox." in line and "import" in line and "._" in line.split("import")[0]
    )

    assert private == []


@pytest.mark.parametrize(
    ("relative", "profiles"),
    [
        (f"{PACKAGE}/firm/loaders.py", QUANT_APP_PROFILES),
        ("pyproject.toml.jinja", QUANT_APP_PROFILES),
        (f"{PACKAGE}/data.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/signals.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/firm/signals.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/results.py", ("alpha-research", "strategy-backtest")),
    ],
)
def test_shared_files_stay_identical(relative: str, profiles: tuple[str, ...]) -> None:
    first, *others = (QUANT_APPS_PACK / profile / "template" / relative for profile in profiles)

    for other in others:
        assert filecmp.cmp(first, other, shallow=False), f"{other} differs from {first}"


def test_the_default_source_pins_this_release() -> None:
    from quant_agent_os import __version__

    for pack in ("quant-apps", "quant-baseline", "agent-tool"):
        questions = (BUILT_IN_PACKS[pack] / "questions.yml").read_text("utf-8")
        assert f"quant-agent-os@v{__version__}\n" in questions, pack


# Each application's own tests, and the chain between them


@pytest.mark.parametrize("profile", QUANT_APP_PROFILES)
def test_each_generated_application_passes_its_own_tests(tmp_path: Path, profile: str) -> None:
    project = _render(tmp_path, profile)

    done = _run_python(project, "-m", "pytest", "-q", "-p", "no:cacheprovider")

    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


def test_a_firm_check_leaves_the_risk_monitors_own_tests_passing(tmp_path: Path) -> None:
    """firm/checks.py invites a firm's own rule; the built-in limits' tests must not run it."""
    project = _render(tmp_path, "risk-monitor", components=["agent-tools"])
    (project / "fund_app" / "firm" / "checks.py").write_text(
        "from ..risk import Check\n"
        "CHECKS = {'always': lambda view: [Check('Always', 1.0, 0.0, True)]}\n"
    )

    done = _run_python(project, "-m", "pytest", "-q", "-p", "no:cacheprovider")

    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


def test_the_chain_runs_from_alpha_to_strategy_to_report(tmp_path: Path) -> None:
    """Each application's output is the next one's input, through ordinary files."""
    alpha, strategy, report = (
        _render(_folder(tmp_path / name), profile)
        for name, profile in (
            ("a", "alpha-research"),
            ("s", "strategy-backtest"),
            ("r", "performance-report"),
        )
    )
    work = _folder(tmp_path / "work")

    scores = work / "scores.csv"
    done = _app(alpha, "export", str(scores), cwd=work)
    assert done.returncode == 0, done.stderr
    assert (work / "scores.csv.meta.json").is_file()

    done = _app(strategy, "backtest", "--signal-file", str(scores), cwd=work)
    assert done.returncode == 0, done.stderr
    returns = next(work.glob("results/*/returns.csv"))

    pytest.importorskip("openpyxl")
    done = _app(report, "report", "--returns", str(returns), cwd=work)
    assert done.returncode == 0, done.stderr
    written = next(work.glob("reports/*/report.md")).read_text()
    assert "| net |" in written and "| gross |" in written


def test_positions_from_a_backtest_feed_the_risk_monitor(tmp_path: Path) -> None:
    strategy = _render(_folder(tmp_path / "s"), "strategy-backtest")
    monitor = _render(_folder(tmp_path / "m"), "risk-monitor")
    work = _folder(tmp_path / "work")

    assert _app(strategy, "backtest", cwd=work).returncode == 0
    weights = next(work.glob("results/*/weights.csv"))
    done = _app(monitor, "report", "--positions", str(weights), cwd=work)

    assert done.returncode == 0, done.stderr
    assert "Exposures" in next(work.glob("reports/*/report.md")).read_text()
