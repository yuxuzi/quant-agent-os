"""`qaos legacy wrap` (decision 0029): the draft parses, lints, and passes its own contract test."""

import ast
import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from quant_app_factory.application.legacy import scan, wrap
from quant_app_factory.domain.legacy import LegacyError
from quant_app_factory.infrastructure.legacy_files import write_inventory
from quant_platform.cli import app as cli

ROOT = Path(__file__).parent / "legacy"
FIXTURE = ROOT / "firm_legacy"
RULES = "E,W,F,I,B,UP,N,SIM,RUF"  # the repository's own, stricter than ruff's defaults


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    """The fixture's inventory, with the module that needs a database left out."""
    path = tmp_path / "legacy.toml"
    write_inventory(scan(FIXTURE), path)
    text = path.read_text()
    path.write_text(
        text.replace(
            'include = true\nname = "daily_prices"', 'include = false\nname = "daily_prices"'
        )
    )
    return path


@pytest.fixture
def drafted(inventory: Path, tmp_path: Path) -> Path:
    out = tmp_path / "legacy_tools"
    result = CliRunner().invoke(cli, ["legacy", "wrap", str(inventory), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "This is a draft" in result.output and "uv run pytest" in result.output
    return out


@pytest.fixture
def legacy_importable(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.syspath_prepend(str(ROOT))
    yield
    for name in [name for name in sys.modules if name.startswith("firm_legacy")]:
        del sys.modules[name]


def test_the_draft_has_every_file_and_each_parses(drafted: Path) -> None:
    files = sorted(str(p.relative_to(drafted)) for p in drafted.rglob("*.py"))

    assert files == [
        "conftest.py",
        "facades.py",
        "server.py",
        "service/__init__.py",
        "service/app.py",
        "stubs.py",
        "test_tools.py",
    ]
    for name in files:
        ast.parse((drafted / name).read_text())
    server = (drafted / "server.py").read_text()
    assert "app.expose(annualised_volatility, returns=Series)" in server
    assert "permissions=[Permission.WRITE_LOCAL_DATA]" in server
    assert "daily_prices" not in server  # excluded in the inventory
    facades = (drafted / "facades.py").read_text()
    assert "with _quiet():" in facades
    assert "# TODO(config): the legacy code reads module settings" in facades


def test_the_draft_passes_ruff_out_of_the_box(drafted: Path) -> None:
    for arguments in (["check"], ["check", "--select", RULES], ["format", "--check"]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *arguments, "--isolated", "--line-length", "100", "."],
            cwd=drafted,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def _pytest(directory: Path) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("COV_CORE")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_tools.py"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=180,
    )


def test_the_generated_contract_test_passes_and_lists_the_open_decisions(drafted: Path) -> None:
    first = _pytest(drafted)

    # check_app passes as drafted, the service included, called over loopback; the draft is
    # not done until every TODO is decided.
    assert "1 failed, 1 passed" in first.stdout, first.stdout + first.stderr
    assert "FAILED test_tools.py::test_no_decision_is_left_open" in first.stdout
    assert "open decisions" in first.stdout

    for name in ("facades.py", "server.py", "stubs.py", "service/app.py"):
        path = drafted / name
        path.write_text(path.read_text().replace("TODO", "DONE"))  # the developer decides
    second = _pytest(drafted)

    assert "2 passed" in second.stdout, second.stdout + second.stderr


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_service_builds_the_object_once_and_answers(
    drafted: Path, legacy_importable: None
) -> None:
    service = _load(drafted / "service" / "app.py", "legacy_draft_service")
    from firm_legacy.risk import RiskModel

    expected = RiskModel().portfolio_var([0.5, 0.5], confidence=0.99)
    with TestClient(service.app) as client:
        built = service.OBJECTS["risk_model"]
        answer = client.post("/risk_model_portfolio_var", json={"weights": [0.5, 0.5]})
        again = client.post(
            "/risk_model_portfolio_var", json={"weights": [0.5, None], "confidence": 0.95}
        )
        assert service.OBJECTS["risk_model"] is built  # built once, in the lifespan

    assert answer.status_code == 200 and answer.json() == expected
    assert again.status_code == 200  # a missing value arrives as NaN; the code decides


def test_wrap_never_overwrites_without_force(inventory: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    wrap(inventory, out)
    (out / "facades.py").write_text("# the developer's edits\n")

    with pytest.raises(LegacyError, match="Would overwrite"):
        wrap(inventory, out)
    assert (out / "facades.py").read_text() == "# the developer's edits\n"
    wrap(inventory, out, force=True)
    assert "def carry_signal" in (out / "facades.py").read_text()


def test_wrap_refuses_an_entry_the_code_no_longer_has(inventory: Path, tmp_path: Path) -> None:
    text = inventory.read_text().replace("firm_legacy.signals.carry_signal", "firm_legacy.gone.f")
    inventory.write_text(text)

    with pytest.raises(LegacyError, match=r"not in .*rescan, or set include = false"):
        wrap(inventory, tmp_path / "out")


def test_a_method_cannot_be_exposed_as_it_is(inventory: Path, tmp_path: Path) -> None:
    text = inventory.read_text().replace('kind = "service"', 'kind = "expose"')
    inventory.write_text(text)

    with pytest.raises(LegacyError, match="make it a facade or a service"):
        wrap(inventory, tmp_path / "out")
