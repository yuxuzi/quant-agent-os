"""The quant-apps pack: one customizable application per stage of the investment process.

Each application is rendered offline and its own tests run in this environment. Its lint, type
checks, and locked install run in the opt-in smoke test, and in `test_factory_smoke`.
"""

from __future__ import annotations

import filecmp
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from quant_analytics import metrics as analytics
from quant_app_factory import AppFactory, AppSpec, PlanningError, PytemplateUvScaffolder
from quant_app_factory import cli as factory_cli
from quant_app_factory.domain.planner import BUILT_IN_PACKS, QUANT_APP_PROFILES, QUANT_APPS_PACK

PACKAGE = "{{ package_name }}"
RETURNS = np.random.default_rng(7).normal(0.0005, 0.01, 504)


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


def _render(tmp_path: Path, profile: str) -> Path:
    report = AppFactory(PytemplateUvScaffolder()).create(_spec(tmp_path, profile), run_checks=False)
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
    assert {"fund_app/cli.py", "fund_app/metrics.py", "fund_app/firm/loaders.py"} <= files
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


# One copy of the numbers


def test_every_application_shares_the_baseline_metrics_exactly() -> None:
    baseline = BUILT_IN_PACKS["quant-baseline"] / "template" / PACKAGE / "metrics.py"

    for profile in QUANT_APP_PROFILES:
        copy = QUANT_APPS_PACK / profile / "template" / PACKAGE / "metrics.py"
        assert filecmp.cmp(baseline, copy, shallow=False), f"{profile} has its own metrics.py"


@pytest.mark.parametrize(
    ("relative", "profiles"),
    [
        (f"{PACKAGE}/tables.py", QUANT_APP_PROFILES),
        (f"{PACKAGE}/firm/loaders.py", QUANT_APP_PROFILES),
        ("pyproject.toml.jinja", QUANT_APP_PROFILES),
        (f"{PACKAGE}/document.py", ("risk-monitor", "performance-report")),
        (f"{PACKAGE}/risk_metrics.py", ("risk-monitor", "performance-report")),
        (f"{PACKAGE}/data.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/signals.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/firm/signals.py", ("alpha-research", "strategy-backtest")),
        (f"{PACKAGE}/evaluation.py", ("alpha-research", "strategy-backtest")),
    ],
)
def test_shared_files_stay_identical(relative: str, profiles: tuple[str, ...]) -> None:
    first, *others = (QUANT_APPS_PACK / profile / "template" / relative for profile in profiles)

    for other in others:
        assert filecmp.cmp(first, other, shallow=False), f"{other} differs from {first}"


def _module(profile: str, name: str) -> Any:
    """Load one standalone module from the pack, as a generated project holds it."""
    path = QUANT_APPS_PACK / profile / "template" / PACKAGE / f"{name}.py"
    source = path.read_text().replace("from .metrics import", "from metrics_for_test import")
    spec = importlib.util.spec_from_loader(f"pack_{name}", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def risk_metrics(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = QUANT_APPS_PACK / "performance-report" / "template" / PACKAGE / "metrics.py"
    spec = importlib.util.spec_from_file_location("metrics_for_test", path)
    assert spec is not None and spec.loader is not None
    metrics = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "metrics_for_test", metrics)
    spec.loader.exec_module(metrics)
    return _module("performance-report", "risk_metrics")


@pytest.mark.parametrize("periods", [252, 52, 12])
def test_risk_measures_agree_with_the_platform(risk_metrics: Any, periods: int) -> None:
    values = list(RETURNS)

    assert risk_metrics.sortino_ratio(RETURNS, periods) == pytest.approx(
        analytics.sortino_ratio(values, periods), abs=1e-10
    )
    assert risk_metrics.calmar_ratio(RETURNS, periods) == pytest.approx(
        analytics.calmar_ratio(values, periods), abs=1e-10
    )


def test_value_at_risk_agrees_with_the_platform(risk_metrics: Any) -> None:
    assert risk_metrics.value_at_risk(RETURNS, 0.05) == pytest.approx(
        analytics.value_at_risk(list(RETURNS), 0.05), abs=1e-12
    )


# Each application's own tests, and the chain between them


@pytest.mark.parametrize("profile", QUANT_APP_PROFILES)
def test_each_generated_application_passes_its_own_tests(tmp_path: Path, profile: str) -> None:
    project = _render(tmp_path, profile)

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
