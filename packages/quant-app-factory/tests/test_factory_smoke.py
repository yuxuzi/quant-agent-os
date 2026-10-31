"""Opt-in smoke test: real projects and their real checks (uv sync, ruff, mypy, pytest).

Set QUANT_FACTORY_SMOKE=1 to run it. It needs uv and network access (for each generated
project's `uv sync`) and takes a minute or two. agent-tool projects install this
checkout of quant-agent-os, so the test exercises the code under review.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quant_app_factory import AppFactory, AppSpec, PytemplateUvScaffolder

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    os.environ.get("QUANT_FACTORY_SMOKE") != "1", reason="set QUANT_FACTORY_SMOKE=1 to run"
)


@pytest.mark.parametrize(
    "kind, extra",
    [
        ("agent-tool", {"tool_name": "count_signals", "quant_agent_os_source": str(ROOT)}),
        ("python-cli", {}),
    ],
)
def test_generated_projects_pass_their_own_checks(
    tmp_path: Path, kind: str, extra: dict[str, str]
) -> None:
    spec = AppSpec.model_validate(
        {
            "name": "smoke-app",
            "kind": kind,
            "description": "A smoke-test project",
            "output_dir": str(tmp_path),
            **extra,
        }
    )

    report = AppFactory(PytemplateUvScaffolder()).create(spec)

    failed = [check for check in report.checks if not check.ok]
    assert report.checks_passed, "\n\n".join(f"{c.command}\n{c.output_tail}" for c in failed)
    assert len(report.checks) == 5
    assert (Path(report.project_path) / "pyproject.toml").is_file()
