"""What a client pays per task must not grow past the recorded baseline (plan step P1.1)."""

import json

import pytest
import re

from agent_efficiency import BASELINE, SKILLS, TASKS, run

# Floats in outputs can differ in their last digits across platforms and library versions.
TOLERANCE = 1.02
METRICS = run()
BASELINE_METRICS = json.loads(BASELINE.read_text("utf-8"))


@pytest.mark.parametrize("task", sorted(TASKS))
def test_every_task_succeeds(task: str) -> None:
    assert METRICS["tasks"][task]["success"], METRICS["tasks"][task]


@pytest.mark.parametrize(
    ("task", "metric"),
    [(task, metric) for task in sorted(TASKS) for metric in ("argument_bytes", "result_bytes")],
)
def test_task_bytes_do_not_regress(task: str, metric: str) -> None:
    now, before = METRICS["tasks"][task][metric], BASELINE_METRICS["tasks"][task][metric]

    assert now <= before * TOLERANCE, f"{task} {metric} grew from {before} to {now}"


@pytest.mark.parametrize("task", sorted(TASKS))
def test_task_calls_do_not_regress(task: str) -> None:
    assert METRICS["tasks"][task]["calls"] <= BASELINE_METRICS["tasks"][task]["calls"]


@pytest.mark.parametrize("server", sorted(BASELINE_METRICS["servers"]))
def test_what_the_model_sees_every_turn_does_not_regress(server: str) -> None:
    now, before = METRICS["servers"][server], BASELINE_METRICS["servers"][server]

    assert now["tools"] <= before["tools"]
    for metric in ("definition_bytes", "instruction_bytes"):
        assert now[metric] <= before[metric] * TOLERANCE, (
            f"{server} {metric} grew from {before[metric]} to {now[metric]}"
        )


def test_what_every_turn_carries_for_the_skills_does_not_regress() -> None:
    now = METRICS["totals"]["skill_description_bytes"]
    before = BASELINE_METRICS["totals"]["skill_description_bytes"]

    assert now <= before * TOLERANCE, f"skill descriptions grew from {before} to {now}"


@pytest.mark.parametrize("task", sorted(TASKS))
def test_each_task_loads_one_skill_that_teaches_its_calls(task: str) -> None:
    measured = METRICS["tasks"][task]
    text = (SKILLS / measured["skill"] / "SKILL.md").read_text("utf-8")
    taught = set(re.findall(r"`([a-z_]+)`", text))

    assert set(measured["tools"]) <= taught, f"{measured['skill']} does not teach {task}'s calls"
    before = BASELINE_METRICS["tasks"][task]["skill_bytes"]
    assert measured["skill_bytes"] <= before * TOLERANCE, (
        f"{measured['skill']} grew from {before} to {measured['skill_bytes']} bytes"
    )
