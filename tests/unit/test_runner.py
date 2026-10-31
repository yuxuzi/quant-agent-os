"""The subprocess runner (B-09): clean environment, timeouts that kill, results not exceptions."""

import os
import sys
import time
from pathlib import Path

import pytest

from quant_agent_os.runtime.runner import OUTPUT_TAIL_CHARS, run_command, scrubbed_environment

PYTHON = sys.executable


def test_a_program_runs_in_its_directory_and_reports_its_output(tmp_path: Path) -> None:
    result = run_command(
        [PYTHON, "-c", "import os, sys; print(os.getcwd()); sys.exit(3)"], cwd=tmp_path, timeout=30
    )

    assert result.exit_code == 3
    assert not result.ok
    assert Path(result.stdout_tail.strip()).resolve() == tmp_path.resolve()
    assert result.output_bytes > 0


def test_secrets_in_the_environment_never_reach_the_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "sk-secret")
    monkeypatch.setenv("QUANT_AGENT_OS_API_KEY", "sk-secret")
    monkeypatch.setenv("UV_INDEX_URL", "https://user:sk-secret@example.invalid/simple")

    result = run_command(
        [PYTHON, "-c", "import os; print(sorted(os.environ))"], cwd=tmp_path, timeout=30
    )

    assert result.ok
    assert "sk-secret" not in result.stdout_tail
    assert "VOYAGE_API_KEY" not in result.stdout_tail
    assert "UV_INDEX_URL" not in result.stdout_tail
    assert set(scrubbed_environment()) <= {name for name in os.environ}


def test_a_timeout_kills_the_program_and_its_children(tmp_path: Path) -> None:
    marker = tmp_path / "survived"
    child = f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('x')"
    parent = (
        f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(60)"
    )
    started = time.perf_counter()

    result = run_command([PYTHON, "-c", parent], cwd=tmp_path, timeout=1)

    assert result.timed_out
    assert result.exit_code is None
    assert time.perf_counter() - started < 20
    time.sleep(3.5)
    assert not marker.exists()


def test_a_missing_program_is_a_result(tmp_path: Path) -> None:
    result = run_command(["definitely-not-a-program-xyz"], cwd=tmp_path, timeout=5)

    assert result.exit_code is None
    assert "Cannot start" in result.stderr_tail


def test_only_the_tail_of_long_output_is_kept(tmp_path: Path) -> None:
    result = run_command([PYTHON, "-c", "print('x' * 100000)"], cwd=tmp_path, timeout=30)

    assert len(result.stdout_tail) == OUTPUT_TAIL_CHARS
    assert result.output_bytes > 100000
