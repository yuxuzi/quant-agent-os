"""The tutorial's lab scripts and sample data, so docs/tutorial.md stays true."""

import io
import json
import re
import runpy
import sys
from pathlib import Path

import pytest

from quant_agent_os.data import DirectoryDatasetStore

ROOT = Path(__file__).resolve().parents[2]
LABS = ROOT / "examples" / "tutorial"
TUTORIAL = ROOT / "docs" / "tutorial.md"


def run_lab(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    script: str,
    *args: str,
) -> str:
    path = LABS / script
    monkeypatch.setattr(sys, "argv", [str(path), *args])
    runpy.run_path(str(path), run_name="__main__")
    return capsys.readouterr().out


def test_analytics_lab_prints_the_numbers_the_tutorial_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = DirectoryDatasetStore(tmp_path)
    store.put("fund", LABS / "data" / "fund_returns.csv")
    store.put("portfolio", LABS / "data" / "portfolio_returns.csv")

    output = run_lab(monkeypatch, capsys, "lab_analytics.py", str(tmp_path))

    for line in ("sharpe_ratio         1.1043", "equities  weight  60%  share of risk  88.9%"):
        assert line in output
        assert line in TUTORIAL.read_text(encoding="utf-8")


def test_analytics_lab_finds_the_datasets_in_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_HOME", str(tmp_path))
    store = DirectoryDatasetStore(tmp_path / "datasets")
    store.put("fund", LABS / "data" / "fund_returns.csv")
    store.put("portfolio", LABS / "data" / "portfolio_returns.csv")

    assert "sharpe_ratio         1.1043" in run_lab(monkeypatch, capsys, "lab_analytics.py")


def test_tutorial_names_no_removed_command() -> None:
    text = TUTORIAL.read_text(encoding="utf-8")

    for removed in ("quant-agent-os --version", "dataset push", "api-key new", "serve-mcp"):
        assert removed not in text


def test_first_tool_lab_passes_the_contract_and_refuses_bad_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = run_lab(monkeypatch, capsys, "lab_first_tool.py")

    assert "Contract check passed" in output
    assert "streak:   3" in output
    assert "Bad call: invalid_input" in output


def test_guardrails_lab_shows_every_refusal_in_order(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = run_lab(monkeypatch, capsys, "lab_guardrails.py")

    statuses = re.findall(r"status: (\w+)", output)
    assert statuses == [
        "denied",
        "approval_required",
        "ok",
        "approval_required",
        "unknown_tool",
        "invalid_input",
    ]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ([{"name": "a"}, {"name": "b"}], "tools: a, b"),
        ({"detail": "No key."}, "refused: No key."),
        ({"status": "denied", "error": "No.", "hint": "Stop."}, "error: No."),
        ({"status": "ok", "output": {"metrics": {"sharpe_ratio": 1.5}}}, "sharpe_ratio: 1.5000"),
        ({"status": "ok", "output": {"spec": {"open_questions": ["a"]}}}, "open questions: 1"),
        ([], "no runs"),
    ],
)
def test_show_makes_rest_responses_readable(
    response: object,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(response)))

    assert expected in run_lab(monkeypatch, capsys, "show.py")


def test_tutorial_links_resolve() -> None:
    text = TUTORIAL.read_text(encoding="utf-8")
    targets = re.findall(r"\]\((?!https?://|#)([^)#]+)", text)

    assert targets
    missing = [target for target in targets if not (TUTORIAL.parent / target).exists()]
    assert not missing
