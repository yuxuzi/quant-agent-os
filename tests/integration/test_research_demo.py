"""The offline Quant Research demo runs end to end and prints a cited report."""

import runpy
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2] / "examples" / "quant-research" / "research_demo.py"


def test_demo_prints_cited_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", [str(DEMO)])

    runpy.run_path(str(DEMO), run_name="__main__")

    output = capsys.readouterr().out
    assert "MCP tools: find_related_signals, get_source_passage" in output
    assert "Order-flow imbalance predicts Short-term reversal" in output
    assert "Data: corpus_snapshot `snap:" in output


SDK_DEMO = DEMO.with_name("sdk_demo.py")


def test_sdk_demo_answers_with_citations_and_a_recorded_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SDK_DEMO)])

    runpy.run_path(str(SDK_DEMO), run_name="__main__")

    output = capsys.readouterr().out
    assert "related: Order-flow imbalance (signal" in output
    assert "cited:   reversal-and-microstructure.md, lines" in output
    assert ": completed, snapshot snap:" in output
