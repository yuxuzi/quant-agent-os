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
        ("alpha-research", {}),
        ("backtest", {}),
        ("report", {}),
        (
            "backtest",
            {"components": ["data-parquet", "agent-tools"], "quant_agent_os_source": str(ROOT)},
        ),
    ],
)
def test_generated_projects_pass_their_own_checks(
    tmp_path: Path, kind: str, extra: dict[str, object]
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


def test_a_database_backtest_passes_its_checks_and_connects(tmp_path: Path) -> None:
    import datetime as dt
    import sqlite3

    database = tmp_path / "prices.db"
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=offset) for offset in range(60)]
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE prices (date TEXT, asset TEXT, price REAL)")
        db.executemany(
            "INSERT INTO prices VALUES (?, ?, ?)",
            [
                (str(date), asset, 100.0 + index)
                for index, date in enumerate(days)
                for asset in "ABCD"
            ],
        )
    db.close()
    spec = AppSpec.model_validate(
        {
            "name": "db-smoke",
            "kind": "backtest",
            "description": "A smoke-test database backtest",
            "output_dir": str(tmp_path),
            "components": ["data-database"],
            "answers": {"db_name": database.as_posix()},
        }
    )

    report = AppFactory(PytemplateUvScaffolder()).create(spec, check_connection=True)

    failed = [check for check in report.checks if not check.ok]
    assert report.checks_passed, "\n\n".join(f"{c.command}\n{c.output_tail}" for c in failed)
    assert report.checks[-1].command == "uv run db-smoke --check-connection"
    assert "Connected to sqlite" in report.checks[-1].output_tail


def test_a_forked_pack_with_a_new_question_builds_a_passing_project(tmp_path: Path) -> None:
    from quant_app_factory.domain.planner import BUILT_IN_PACKS
    from quant_app_factory.infrastructure.pack_files import fork_pack

    pack = fork_pack(
        BUILT_IN_PACKS["quant-baseline"],
        "backtest",
        tmp_path / "pack",
        pack_id="desk-backtest",
        profile_id="desk",
    )
    questions = pack / "questions.yml"
    questions.write_text(
        questions.read_text().replace(
            "frequency:",
            "desk:\n  type: str\n  help: Trading desk\n  default: equities\n\nfrequency:",
            1,
        )
    )
    profile = pack / "desk" / "profile.toml"
    profile.write_text(profile.read_text().replace('"frequency",', '"desk", "frequency",'))
    (pack / "template" / "DESK.md.jinja").write_text("Desk: {{ desk }}\n")
    out = tmp_path / "out"
    out.mkdir()
    spec = AppSpec.model_validate(
        {
            "name": "desk-app",
            "template": pack.as_posix(),
            "profile": "desk",
            "description": "A forked backtest",
            "output_dir": str(out),
            "answers": {"desk": "rates"},
        }
    )

    report = AppFactory(PytemplateUvScaffolder()).create(spec)

    failed = [check for check in report.checks if not check.ok]
    assert report.checks_passed, "\n\n".join(f"{c.command}\n{c.output_tail}" for c in failed)
    assert (Path(report.project_path) / "DESK.md").read_text() == "Desk: rates\n"
    assert (Path(report.project_path) / "desk_app" / "backtest.py").is_file()
