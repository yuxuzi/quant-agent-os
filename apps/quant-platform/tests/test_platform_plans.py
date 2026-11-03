"""Plans in the tenants file (B-36, decision 0015): entitlements, limits, and their checks."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.runs import MemoryRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_platform.cli import app as cli
from quant_platform.tenants import (
    Plan,
    TenantCatalogs,
    TenantConfig,
    TenantsFile,
    TenantsFileError,
    dump_tenants,
    load_tenants_file,
    save_tenants,
)
from quant_research import open_research

PLANS = """
[plans.team]
toolsets = ["research", "analytics"]
calls_per_minute = 120
monthly_calls = 50_000

[plans.analytics-only]
toolsets = ["analytics"]
"""


def _names(executor: ToolExecutor) -> list[str]:
    return sorted(spec.name for spec in executor.registry.specs())


def test_plans_round_trip_with_the_tenants_that_name_them(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    whole = TenantsFile(
        plans={
            "team": Plan(toolsets=["research", "analytics"], calls_per_minute=120),
            "free": Plan(monthly_calls=1_000, max_output_bytes=50_000),
        },
        tenants={"acme": TenantConfig(plan="team", toolsets=["research"])},
    )
    save_tenants(path, whole)

    assert load_tenants_file(path) == whole
    text = dump_tenants(whole)
    assert "[plans.team]" in text and 'plan = "team"' in text
    assert "monthly_calls = 1000" in text


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ('[tenants.acme]\nplan = "gold"\n', "plan 'gold' is not among the plans"),
        (
            '[tenants.acme]\nplan = "analytics-only"\ntoolsets = ["research"]\n',
            "lists research, which its plan 'analytics-only' does not include",
        ),
        ('[plans.packed]\npacks = ["factors-v1"]\n', "Knowledge packs are not available yet"),
        ('[plans."Gold Plan"]\n', "Plan name 'Gold Plan'"),
        ("[plans.zero]\ncalls_per_minute = 0\n", "calls_per_minute"),
    ],
)
def test_the_tenants_file_refuses_bad_plans(tmp_path: Path, extra: str, match: str) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(PLANS + extra)

    with pytest.raises(TenantsFileError, match=match):
        load_tenants_file(path)


def test_a_tenant_is_served_only_its_plans_toolsets(tmp_path: Path) -> None:
    store = tmp_path / "kb.db"
    open_research(store).close()
    whole = TenantsFile.model_validate(
        {
            "plans": {"analytics-only": {"toolsets": ["analytics"]}},
            "tenants": {
                "acme": {"store": str(store), "plan": "analytics-only"},
                "open": {"store": str(store)},
            },
        }
    )
    catalogs = TenantCatalogs(
        whole.tenants,
        run_store=MemoryRunStore(),
        datasets_root=tmp_path / "datasets",
        plans=whole.plans,
    )
    try:
        planned, unplanned = catalogs.surface("acme"), catalogs.surface("open")
        assert planned is not None and unplanned is not None
        assert set(planned.toolsets.values()) == {"analytics"}
        assert {"research", "analytics"} <= set(unplanned.toolsets.values())
        assert "find_related_signals" not in _names(planned.executor)
        assert not any(resource.tool == "get_source_passage" for resource in planned.resources)
    finally:
        catalogs.close()


def test_tenant_add_takes_a_known_plan_and_keeps_the_plans(tmp_path: Path) -> None:
    path = tmp_path / "tenants.toml"
    path.write_text(PLANS)
    runner = CliRunner()

    added = runner.invoke(cli, ["tenant", "add", "acme", "--plan", "team", "--tenants", str(path)])
    unknown = runner.invoke(
        cli, ["tenant", "add", "beta", "--plan", "gold", "--tenants", str(path)]
    )
    widened = runner.invoke(
        cli,
        [
            "tenant",
            "add",
            "gamma",
            "--plan",
            "analytics-only",
            "--toolsets",
            "research",
            "--tenants",
            str(path),
        ],
    )
    listed = runner.invoke(cli, ["tenant", "list", "--tenants", str(path)])
    whole = load_tenants_file(path)

    assert added.exit_code == 0 and whole.tenants["acme"].plan == "team"
    assert set(whole.plans) == {"team", "analytics-only"}
    assert unknown.exit_code == 1 and "not among the plans" in unknown.output
    assert widened.exit_code == 1 and "beta" not in whole.tenants and "gamma" not in whole.tenants
    assert listed.exit_code == 0 and "team" in listed.output
