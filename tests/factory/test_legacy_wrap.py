"""`qaos legacy wrap` (decision 0029): the draft parses, lints, and passes its own contract test."""

import ast
import importlib.util
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from quant_agent_os.testing import check_tool
from quant_agent_os.tools.contracts import ToolContractError
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
        "__init__.py",
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
    assert "with _legacy():" in facades
    assert "# TODO(config): the legacy code reads module settings" in facades
    # A memo cache makes a facade with a note, not a service.
    assert "facades.index_level" in server
    assert "index_level" not in (drafted / "stubs.py").read_text()
    assert "# TODO(review): reads through a module cache (_CACHE)" in facades


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

    for name in ("facades.py", "server.py", "stubs.py", "service/app.py", "test_tools.py"):
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


def test_concurrent_facades_leave_stdout_as_it_was(
    drafted: Path, legacy_importable: None, capsys: pytest.CaptureFixture[str]
) -> None:
    facades = _load(drafted / "facades.py", "legacy_draft_facades")
    first_inside, second_inside, first_done = (threading.Event() for _ in range(3))

    def legacy(prices: Any, lookback: Any) -> None:
        print("legacy output", lookback)
        if lookback == "first":
            first_inside.set()
            second_inside.wait(0.5)  # with no lock, the second call is inside by now
        else:
            second_inside.set()
            first_done.wait(0.5)  # with no lock, it leaves last and restores stderr

    facades.legacy_carry_signal = legacy
    stdout = sys.stdout

    def call(lookback: str) -> None:
        facades.carry_signal(prices=None, lookback=lookback)
        if lookback == "first":
            first_done.set()

    first = threading.Thread(target=call, args=("first",))
    first.start()
    assert first_inside.wait(5)
    second = threading.Thread(target=call, args=("second",))
    second.start()
    first.join(5)
    second.join(5)

    # redirect_stdout is process-wide: two calls at once used to leave stdout on stderr.
    assert sys.stdout is stdout
    captured = capsys.readouterr()
    assert "legacy output" not in captured.out
    assert captured.err.count("legacy output") == 2


@pytest.fixture
def draft_package(drafted: Path, legacy_importable: None) -> Iterator[Any]:
    """The drafted package, imported as a firm's server would import it."""
    sys.path.insert(0, str(drafted.parent))
    yield importlib.import_module("legacy_tools.test_tools")
    sys.path.remove(str(drafted.parent))
    for name in [name for name in sys.modules if name.startswith("legacy_tools")]:
        del sys.modules[name]


def test_examples_cover_optional_data_and_placeholders_match_the_type(
    drafted: Path, draft_package: Any
) -> None:
    examples = draft_package.EXAMPLES
    test_text = (drafted / "test_tools.py").read_text()
    facades = (drafted / "facades.py").read_text()

    # An optional data parameter gets a reference; with none, momentum read the warehouse.
    assert examples["momentum"] == {"panel": {"dataset": "sample_panel"}}
    assert "panel: pd.DataFrame | None = None" in facades
    # An untyped name is a string, guessed from the name, and the guess is an open decision.
    assert examples["index_level"] == {"name": "example"}
    assert "def index_level(name: str)" in facades
    assert '"name": "example",  # TODO(data)' in test_text
    # A number where a name belongs is refused, where 1.0 used to pass.
    app = draft_package.server.build_app(draft_package.server.SERVICE_URL)
    (spec,) = [spec for spec in app.tools(draft_package.STORE) if spec.name == "index_level"]
    check_tool(spec, {"name": "example"})
    with pytest.raises(ToolContractError, match="expected 'ok'"):
        check_tool(spec, {"name": 1.0})


def test_a_whole_table_is_returned_one_date_at_a_time(drafted: Path, draft_package: Any) -> None:
    facades = (drafted / "facades.py").read_text()
    server = (drafted / "server.py").read_text()

    # momentum returns a dates-by-names panel, which grows past the output limit with history.
    assert "as_of: str | None = None" in facades
    assert "return _cross_section(result, as_of)" in facades
    # cross_section is served as it is, so the draft can only say so.
    assert "TODO(review): cross_section returns a whole dates-by-names table" in server
    app = draft_package.server.build_app(draft_package.server.SERVICE_URL)
    (spec,) = [spec for spec in app.tools(draft_package.STORE) if spec.name == "momentum"]
    record = check_tool(spec, draft_package.EXAMPLES["momentum"])
    assert set(record.output["result"]) == {"date", "values"}


def test_a_firms_server_imports_the_draft_as_a_package(drafted: Path) -> None:
    # The draft used to import facades and stubs by bare name, so a firm's server had to put
    # the draft's own folder on sys.path. Now only the folder above it (the server's) is needed.
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}  # the legacy code, as if installed
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from legacy_tools.server import build_app; print(' '.join(build_app().names))",
        ],
        cwd=drafted.parent,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "carry_signal" in result.stdout and "risk_model_portfolio_var" in result.stdout
    assert "from . import facades, stubs" in (drafted / "server.py").read_text()


def test_the_draft_folder_must_be_importable(inventory: Path, tmp_path: Path) -> None:
    with pytest.raises(LegacyError, match="such as legacy_tools"):
        wrap(inventory, tmp_path / "legacy-tools")


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
