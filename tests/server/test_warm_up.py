"""Serving loads the analytics stack first, so a cold start never spends a call's time limit."""

from pathlib import Path

import pytest

import quant_analytics.computed as computed
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.tools import PermissionPolicy
from quant_platform.composition import compose
from quant_platform.serve import warm_up


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(computed, "warm_up", lambda: calls.append("warm"))
    return calls


def test_a_catalog_with_analytics_warms_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting(monkeypatch)

    warm_up(compose(policy=PermissionPolicy(), datasets=DirectoryDatasetStore(tmp_path)))

    assert calls == ["warm"]


def test_a_catalog_without_analytics_warms_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting(monkeypatch)

    warm_up(compose(policy=PermissionPolicy()))

    assert calls == []


def test_a_failed_warm_up_is_left_for_the_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise ImportError("no backend")

    monkeypatch.setattr(computed, "warm_up", broken)

    warm_up(compose(policy=PermissionPolicy(), datasets=DirectoryDatasetStore(tmp_path)))


def test_the_warm_up_runs_the_performance_tool_on_fixed_numbers() -> None:
    computed.warm_up()
