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
from quant_app_factory.infrastructure.legacy_files import load_inventory, write_inventory
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
    files = sorted(p.relative_to(drafted).as_posix() for p in drafted.rglob("*.py"))

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


def _ruff_problems(directory: Path) -> list[str]:
    problems = []
    # Ruff's defaults, then the repository's stricter rules; line length is the firm's choice.
    for arguments in ([], ["--select", RULES, "--ignore", "E501"]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", *arguments, "."],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            problems.append(result.stdout + result.stderr)
    return problems


def test_the_draft_passes_ruff_out_of_the_box(drafted: Path) -> None:
    assert _ruff_problems(drafted) == []


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
        # Untyped data goes to the legacy code as pandas, sent as values by date.
        weights = {"2024-01-02": 0.5, "2024-01-03": 0.5}
        answer = client.post("/risk_model_portfolio_var", json={"weights": weights})
        again = client.post(
            "/risk_model_portfolio_var",
            json={"weights": {**weights, "2024-01-03": None}, "confidence": 0.95},
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


DESK = '''\
import time

WINDOW = 20
DEFAULTS = {"scale": 2.0}


class Engine:
    """A risk engine with a slow start and a memo."""

    def __init__(self, scale=DEFAULTS["scale"]):
        time.sleep(0.01)
        self.scale = scale
        self._memo = {}

    def stress(self, returns, prices, shock=-0.1):
        """The book's value after a shock, plus the mean return."""
        return float((prices.iloc[-1] * (1 + shock)).sum() * self.scale + returns.mean())

    def exposures(self, prices):
        """Each asset's mean one-period return."""
        return prices.pct_change().mean()


class Blender:
    """No costly state: each method becomes a facade that builds one."""

    def __init__(self, weight=0.5):
        self.weight = weight

    def blend(self, signal, other):
        """A weighted blend of two signals."""
        return signal * self.weight + other * (1 - self.weight)


def scaled(returns, factor=WINDOW, *extra, **options):
    """Returns times a factor."""
    print("scaling")
    return returns * factor
'''


def test_pandas_services_method_facades_and_odd_signatures_pass_as_drafted(
    tmp_path: Path,
) -> None:
    package = tmp_path / "src" / "desk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "engine.py").write_text(DESK)
    inventory = tmp_path / "legacy.toml"
    write_inventory(scan(package), inventory)
    kinds = {e.name: (e.kind.value, e.data) for e in load_inventory(inventory).entries}
    assert kinds == {
        "blender_blend": ("facade", {"signal": "series"}),
        "engine_exposures": ("service", {"prices": "series"}),
        "engine_stress": ("service", {"returns": "series", "prices": "series"}),
        "scaled": ("facade", {"returns": "series"}),
    }
    text = inventory.read_text().replace(
        'data = { prices = "series" }', 'data = { prices = "frame" }'
    )
    text = text.replace(
        'data = { returns = "series", prices = "series" }',
        'data = { returns = "series", prices = "frame" }',
    )
    inventory.write_text(text)

    out = tmp_path / "tools"
    wrap(inventory, out)
    result = _pytest(out)

    assert "1 failed, 1 passed" in result.stdout, result.stdout + result.stderr
    assert "FAILED test_tools.py::test_no_decision_is_left_open" in result.stdout
    stubs = (out / "stubs.py").read_text()
    assert "def engine_stress(returns: pd.Series, prices: pd.DataFrame" in stubs
    facades = (out / "facades.py").read_text()
    assert "def blender_blend(*, signal: pd.Series, other: Any, weight: float = 0.5)" in facades
    assert "the legacy code also takes **options, *extra" in facades
    assert _ruff_problems(out) == []


LOADER = '''\
import time


class Engine:
    """Loads once, then answers from memory; it refuses to answer before load()."""

    def __init__(self, lookback=500):
        self.lookback = lookback
        self.book = None
        self._cache = {}

    def load(self):
        time.sleep(0.01)
        self.book = {"macro": 2.0}
        return self

    def var(self, desk):
        """The desk's value at risk."""
        if self.book is None:
            raise RuntimeError("call load() first")
        return self.book[desk]
'''


def test_the_service_calls_a_set_up_method_it_does_not_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "src" / "riskdesk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "engine.py").write_text(LOADER)
    inventory = tmp_path / "legacy.toml"
    write_inventory(scan(package), inventory)
    kinds = {e.name: e.kind.value for e in load_inventory(inventory).entries}
    assert kinds == {"engine_load": "service", "engine_var": "service"}
    inventory.write_text(
        inventory.read_text().replace(
            'include = true\nname = "engine_load"', 'include = false\nname = "engine_load"'
        )
    )

    out = tmp_path / "tools"
    wrap(inventory, out)
    service = (out / "service" / "app.py").read_text()

    assert 'OBJECTS["engine"].load()' in service
    assert "TODO(config): Engine.load() looks like set-up" in service
    monkeypatch.syspath_prepend(str(package.parent))
    spec = importlib.util.spec_from_file_location("riskdesk_service", out / "service" / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "riskdesk_service", module)  # pydantic resolves hints here
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        answer = client.post("/engine_var", json={"desk": "macro"})
    assert answer.status_code == 200 and answer.json() == 2.0
    for name in [name for name in sys.modules if name.startswith("riskdesk")]:
        del sys.modules[name]
