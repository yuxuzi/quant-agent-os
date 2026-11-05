"""End-to-end workflow on synthetic data (no network, no model)."""

import math
import runpy
import sys
from pathlib import Path

import pytest
from portfolio_research import DEFAULT_CONFIG, load_config, run
from portfolio_tools import SeriesInput, build_registry, max_drawdown

from quant_agent_os.reporting import render_markdown


def test_example_produces_a_complete_evidence_linked_report() -> None:
    config = load_config(DEFAULT_CONFIG)

    report = run(config)
    markdown = render_markdown(report)

    returns = config["series"]["returns"]
    expected = math.prod(1 + r for r in returns) ** (12 / len(returns)) - 1
    assert report.succeeded
    assert report.findings[0].kind == "computed"
    assert f"{expected:.2%}" in report.findings[0].statement
    assert report.findings[0].sources[0].source_id == "synthetic-balanced-2025"
    assert report.limitations
    for section in ("## Findings", "## Warnings", "## Limitations", "## Provenance"):
        assert section in markdown


def test_short_series_produces_warnings() -> None:
    config = load_config(DEFAULT_CONFIG)
    config["series"]["returns"] = [0.01, 0.02, -0.01]

    report = run(config)

    assert any("unreliable" in warning for warning in report.warnings)


def test_an_unknown_tool_is_reported_and_nothing_else_is_lost() -> None:
    config = load_config(DEFAULT_CONFIG)
    config["request"]["metrics"] = ["place_trade", "max_drawdown"]

    report = run(config)

    assert [call.status for call in report.tool_calls] == ["unknown_tool", "ok"]
    assert any("place_trade: unknown_tool" in warning for warning in report.warnings)
    assert report.findings


def test_your_own_model_example_recovers_from_a_failed_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = Path(__file__).parents[2] / "examples" / "your-own-model" / "drive_tools.py"
    monkeypatch.setattr(sys, "argv", [str(script)])

    runpy.run_path(str(script), run_name="__main__")

    output = capsys.readouterr().out
    assert "analyze_performance: invalid_input" in output
    assert "analyze_performance: ok" in output
    assert "Sharpe ratio, computed by the tool: 1.44" in output


def test_max_drawdown_known_answer() -> None:
    # Wealth path 1.10 -> 0.55 -> 0.66: the worst decline is 0.55 / 1.10 - 1 = -50%.
    result = max_drawdown.function(SeriesInput(series_id="s", returns=[0.1, -0.5, 0.2]))

    assert result.value == pytest.approx(-0.5)


def test_report_metadata_identifies_the_run_and_registry() -> None:
    report = run(load_config(DEFAULT_CONFIG))

    assert report.metadata.run_id
    assert report.metadata.registry_digest == build_registry().digest()
    assert {call.run_id for call in report.tool_calls} == {report.metadata.run_id}
    assert f"Run `{report.metadata.run_id}`" in render_markdown(report)
