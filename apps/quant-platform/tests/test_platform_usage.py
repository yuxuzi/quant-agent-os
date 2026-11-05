"""`quant-platform usage export` (B-36 P7.1): usage per tenant, from the run store."""

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from quant_agent_os.runs import SqliteRunStore, UsageEvent
from quant_agent_os.runtime import ToolCallRecord
from quant_platform.cli import app as cli


def _record(store: SqliteRunStore, call_id: str, tenant: str, tool: str, at: datetime) -> None:
    record = ToolCallRecord(
        tool=tool,
        tool_version="1.0.0",
        arguments={"query": "secret thesis"},
        status="ok",
        output={"answer": "x"},
        duration_ms=2.0,
        call_id=call_id,
        run_id=f"run-{call_id}",
        started_at=at,
    )
    store.add_call(record, UsageEvent.of(record, interface="rest", tenant=tenant))


def _runs(tmp_path: Path) -> Path:
    path = tmp_path / "runs.db"
    with SqliteRunStore(path) as store:
        _record(store, "a", "acme", "search", datetime(2026, 10, 31, 12, tzinfo=UTC))
        _record(store, "b", "acme", "search", datetime(2026, 11, 2, 9, tzinfo=UTC))
        _record(store, "c", "acme", "report", datetime(2026, 11, 3, 9, tzinfo=UTC))
        _record(store, "d", "beta", "search", datetime(2026, 11, 2, 9, tzinfo=UTC))
    return path


NOVEMBER = ["--from", "2026-11-01", "--to", "2026-12-01"]


def test_usage_export_writes_one_tenants_calls_in_the_window_as_jsonl(tmp_path: Path) -> None:
    runs = _runs(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "hosted",
            "usage",
            "export",
            "--runs",
            str(runs),
            "--tenant",
            "acme",
            *NOVEMBER,
            "--format",
            "jsonl",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [(row["call_id"], row["tenant"], row["billable"]) for row in rows] == [
        ("b", "acme", True),
        ("c", "acme", True),
    ]
    assert "secret" not in result.stdout


def test_usage_export_totals_by_tool_as_csv(tmp_path: Path) -> None:
    runs = _runs(tmp_path)

    result = CliRunner().invoke(
        cli, ["hosted", "usage", "export", "--runs", str(runs), *NOVEMBER, "--by", "tool"]
    )

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert [(row["tenant"], row["group"], row["calls"]) for row in rows] == [
        ("acme", "report", "1"),
        ("acme", "search", "1"),
        ("beta", "search", "1"),
    ]


def test_usage_export_refuses_a_missing_store_and_bad_choices(tmp_path: Path) -> None:
    runner = CliRunner()

    missing = runner.invoke(cli, ["hosted", "usage", "export", "--runs", str(tmp_path / "none.db")])
    bad_by = runner.invoke(
        cli, ["hosted", "usage", "export", "--runs", str(_runs(tmp_path)), "--by", "week"]
    )

    assert missing.exit_code == 1 and "no run store" in missing.output
    assert not (tmp_path / "none.db").exists()
    assert bad_by.exit_code == 2
