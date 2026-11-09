"""A firm's registered functions on the platform's server (decision 0024): compose and serve."""

from pathlib import Path
from typing import Any

import pytest

from quant_agent_os.api import add_key
from quant_agent_os.data import DirectoryDatasetStore
from quant_agent_os.registration import FirmApp, Series
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.tools import (
    Caller,
    FileAccessPolicy,
    Permission,
    PermissionPolicy,
    ToolContractError,
    ToolRegistry,
)
from quant_agent_os.tools.access import acting_as
from quant_agent_os.tools.registry import DuplicateToolError
from quant_platform import Catalog, compose, serve_apps
from quant_platform.composition import PLATFORM_INSTRUCTIONS


def worst(returns: list[float]) -> float:
    """The worst period's return."""
    return min(returns)


def _app(**options: Any) -> FirmApp:
    app = FirmApp("risk", instructions="Use worst for a desk's worst period.", **options)
    app.expose(worst, returns=Series)
    return app


@pytest.fixture
def two_desks(datasets: DirectoryDatasetStore, tmp_path: Path) -> DirectoryDatasetStore:
    other = tmp_path / "other.csv"
    other.write_text("month,fund\n2024-01,0.01\n2024-02,-0.02\n2024-03,0.03\n")
    datasets.put("other", other)
    return datasets


def scale(value: float, factor: float = 2.0) -> float:
    """Multiply a value."""
    return value * factor


def test_an_apps_tools_are_served_as_the_firm_toolset_with_its_instructions() -> None:
    app = FirmApp("math", instructions="Use worst for a desk's worst period.")
    app.expose(scale)

    catalog = compose(policy=PermissionPolicy(), apps=[app])

    assert catalog.toolsets["scale"] == "firm"
    assert "analyze_risk" not in catalog.toolsets  # no datasets given to the platform
    assert (
        catalog.instructions == f"{PLATFORM_INSTRUCTIONS}\n\nUse worst for a desk's worst period."
    )


def test_an_app_reads_the_datasets_the_platform_serves(datasets: DirectoryDatasetStore) -> None:
    pytest.importorskip("empyrical")
    catalog = compose(policy=PermissionPolicy(), datasets=datasets, apps=[_app()])
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)

    record = executor.execute(
        ToolCall(tool="worst", arguments={"returns": {"dataset": "returns", "column": "fund"}})
    )

    assert record.status == "ok" and record.output is not None
    assert record.output["result"] == -0.015
    assert catalog.toolsets["analyze_risk"] == "analytics"


def test_an_app_reading_data_with_no_datasets_is_refused_at_start_up() -> None:
    with pytest.raises(ToolContractError, match="with datasets="):
        compose(policy=PermissionPolicy(), datasets=None, apps=[_app()])


def test_an_app_cannot_replace_a_served_name_and_a_prefix_avoids_it(
    datasets: DirectoryDatasetStore,
) -> None:
    pytest.importorskip("empyrical")
    clash = FirmApp("risk")
    clash.expose(worst, name="analyze_risk", returns=Series)

    with pytest.raises(DuplicateToolError, match="analyze_risk"):
        compose(policy=PermissionPolicy(), datasets=datasets, apps=[clash])
    with pytest.raises(DuplicateToolError, match="worst"):
        compose(policy=PermissionPolicy(), datasets=datasets, apps=[_app(), _app()])
    prefixed = compose(
        policy=PermissionPolicy(), datasets=datasets, apps=[_app(), _app(prefix=True)]
    )
    assert {"worst", "risk_worst"} <= set(prefixed.toolsets)


def test_a_caller_reaches_only_the_datasets_its_access_allows(
    two_desks: DirectoryDatasetStore, tmp_path: Path
) -> None:
    pytest.importorskip("empyrical")
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.growth]\ntools = ["worst"]\ndatasets = ["returns"]\n')
    catalog = compose(
        policy=PermissionPolicy(),
        datasets=two_desks,
        apps=[_app()],
        access=FileAccessPolicy(rules),
    )
    executor = ToolExecutor(ToolRegistry(catalog.tools), catalog.policy)

    def call(dataset: str) -> Any:
        with acting_as(Caller("alice", groups=frozenset({"growth"}))):
            return executor.execute(
                ToolCall(
                    tool="worst", arguments={"returns": {"dataset": dataset, "column": "fund"}}
                )
            )

    allowed, refused = call("returns"), call("other")

    assert allowed.status == "ok"
    assert refused.status == "error"
    assert refused.error == "UnknownDatasetError: No dataset 'other'; the datasets are returns."


def test_serve_apps_takes_its_policy_and_access_file_from_the_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, datasets: DirectoryDatasetStore
) -> None:
    rules = tmp_path / "access.toml"
    rules.write_text('[groups.growth]\ntools = ["worst"]\ndatasets = ["returns"]\n')
    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(rules))
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')
    served: dict[str, Any] = {}

    def fake_serve(catalog: Catalog, *, host: str, port: int) -> None:
        served.update(catalog=catalog, host=host, port=port)

    monkeypatch.setattr("quant_platform.serve.serve_http", fake_serve)
    serve_apps([_app()], datasets=datasets, port=9000)

    catalog: Catalog = served["catalog"]
    assert served["host"] == "127.0.0.1" and served["port"] == 9000
    assert isinstance(catalog.access, FileAccessPolicy) and catalog.access.path == rules
    assert Permission.NETWORK in catalog.policy.granted
    assert catalog.toolsets["worst"] == "firm"


def test_serve_apps_serves_over_http_with_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, datasets: DirectoryDatasetStore
) -> None:
    keys = tmp_path / "keys.toml"
    add_key(keys, "alice", ["tools:call"])
    monkeypatch.setenv("QUANT_AGENT_OS_API_KEYS_FILE", str(keys))
    monkeypatch.setenv("QUANT_AGENT_OS_RUNS_FILE", str(tmp_path / "runs.db"))
    served: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        served.update(host=host, port=port)

    monkeypatch.setattr("quant_platform.serve.run_http", fake_run)
    serve_apps([_app()], datasets=datasets)

    assert served == {"host": "127.0.0.1", "port": 8000}
