"""End-to-end workflow on synthetic data with a fake provider (no network, no LLM)."""

import json
import math

import pytest
from portfolio_research import DEFAULT_CONFIG, load_config, run
from portfolio_tools import ReturnsInput, build_registry, max_drawdown

from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.reporting import render_markdown
from quant_agent_os.runtime.orchestrator import Orchestrator, PlanningError


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


def _orchestrator(plan: dict[str, object], max_steps: int = 4) -> Orchestrator:
    provider = FakeModelProvider([json.dumps(plan)])
    return Orchestrator(build_registry(), provider, max_steps=max_steps)


def test_plans_with_unknown_tools_are_rejected_before_execution() -> None:
    plan = {"goal": "g", "steps": [{"tool": "place_trade", "arguments": {}}]}

    with pytest.raises(PlanningError, match="place_trade"):
        _orchestrator(plan).run("g")


def test_plans_over_the_step_limit_are_rejected() -> None:
    step = {"tool": "max_drawdown", "arguments": {"series_id": "s", "returns": [0.1, 0.2]}}
    plan = {"goal": "g", "steps": [step] * 3}

    with pytest.raises(PlanningError, match="limit is 2"):
        _orchestrator(plan, max_steps=2).run("g")


def test_malformed_provider_output_is_rejected() -> None:
    orchestrator = Orchestrator(build_registry(), FakeModelProvider(["not json"]))

    with pytest.raises(PlanningError, match="invalid plan"):
        orchestrator.run("g")


def test_planning_prompt_lists_only_registered_tools() -> None:
    provider = FakeModelProvider(
        [
            json.dumps(
                {
                    "goal": "g",
                    "steps": [
                        {
                            "tool": "max_drawdown",
                            "arguments": {"series_id": "s", "returns": [0.1, -0.2]},
                        }
                    ],
                }
            )
        ]
    )

    Orchestrator(build_registry(), provider).run("g")

    assert '"name": "annualized_return"' in provider.prompts[0]
    assert '"name": "max_drawdown"' in provider.prompts[0]


def test_max_drawdown_known_answer() -> None:
    # Wealth path 1.10 -> 0.55 -> 0.66: the worst decline is 0.55 / 1.10 - 1 = -50%.
    result = max_drawdown.function(ReturnsInput(series_id="s", returns=[0.1, -0.5, 0.2]))

    assert result.value == pytest.approx(-0.5)
