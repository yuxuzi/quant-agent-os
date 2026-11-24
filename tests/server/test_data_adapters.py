"""Stub adapters (decision 0037): `qaos data adapter new` writes a typed adapter and a test."""

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from quant_platform import cli as platform_cli

FILLED = """import datetime as dt


class ReturnsFeedAdapter:
    def read(self, start: dt.date, end: dt.date) -> dict[str, list]:
        return {
            "entity": ["FUND"],
            "date": [start],
            "as_of": [start],
            "value": [0.01],
        }
"""


def _new(tmp_path: Path, *extra: str, name: str = "returns_feed") -> object:
    args = ["data", "adapter", "new", name, "--table", "returns", "--out", str(tmp_path), *extra]
    return CliRunner().invoke(platform_cli.app, args)


def _pytest(folder: Path) -> int:
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(folder)],
        capture_output=True,
        text=True,
        check=False,
        cwd=folder,
    )
    return run.returncode


def test_the_adapter_and_its_test_are_written(tmp_path: Path) -> None:
    result = _new(tmp_path)

    assert result.exit_code == 0, result.output
    adapter = (tmp_path / "returns_feed_adapter.py").read_text("utf-8")
    assert "class ReturnsFeedAdapter" in adapter
    assert "raise NotImplementedError" in adapter
    assert "entity, date, as_of, value" in adapter
    test = (tmp_path / "test_returns_feed_adapter.py").read_text("utf-8")
    assert 'validate("returns", columns) == []' in test


def test_the_generated_test_fails_until_the_adapter_is_filled(tmp_path: Path) -> None:
    _new(tmp_path)
    assert _pytest(tmp_path) != 0

    (tmp_path / "returns_feed_adapter.py").write_text(FILLED, "utf-8")

    assert _pytest(tmp_path) == 0


def test_an_existing_file_is_not_overwritten_without_force(tmp_path: Path) -> None:
    _new(tmp_path)
    (tmp_path / "returns_feed_adapter.py").write_text(FILLED, "utf-8")

    refused = _new(tmp_path)

    assert refused.exit_code == 1
    assert "pass --force" in refused.output
    assert (tmp_path / "returns_feed_adapter.py").read_text("utf-8") == FILLED


def test_force_overwrites_the_existing_files(tmp_path: Path) -> None:
    _new(tmp_path)
    (tmp_path / "returns_feed_adapter.py").write_text(FILLED, "utf-8")

    forced = _new(tmp_path, "--force")

    assert forced.exit_code == 0, forced.output
    assert "raise NotImplementedError" in (tmp_path / "returns_feed_adapter.py").read_text("utf-8")


def test_refused_names_and_tables_write_nothing(tmp_path: Path) -> None:
    bad_name = _new(tmp_path, name="Returns-Feed")
    bad_table = CliRunner().invoke(
        platform_cli.app,
        ["data", "adapter", "new", "feed", "--table", "bogus", "--out", str(tmp_path)],
    )

    assert bad_name.exit_code == 1
    assert bad_table.exit_code == 1
    assert "No standard table 'bogus'" in bad_table.output
    assert list(tmp_path.iterdir()) == []
