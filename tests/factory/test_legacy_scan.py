"""`qaos legacy scan` (decision 0029): legacy code inventoried statically, never imported."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.tools import Permission
from quant_app_factory.application.legacy import scan
from quant_app_factory.domain.legacy import Kind, LegacyError, Trait, classify
from quant_app_factory.infrastructure.legacy_files import (
    dump_inventory,
    load_inventory,
    write_inventory,
)
from quant_platform.cli import app as cli

FIXTURE = Path(__file__).parent / "legacy" / "firm_legacy"


@pytest.fixture(autouse=True)
def _not_imported(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start without the fixture imported, and prove the scan did not import it."""
    for name in [name for name in sys.modules if name.startswith("firm_legacy")]:
        monkeypatch.delitem(sys.modules, name)
    yield
    assert not [name for name in sys.modules if name.startswith("firm_legacy")]


def _entries() -> dict[str, tuple[Kind, list[str]]]:
    inventory = scan(FIXTURE)
    return {
        entry.name: (entry.kind, classify(inventory.found[entry.target])[1])
        for entry in inventory.entries
    }


def test_each_callable_is_classified_with_its_reasons() -> None:
    entries = _entries()

    assert {name: kind for name, (kind, _) in entries.items()} == {
        "annualised_volatility": Kind.EXPOSE,
        "cross_section": Kind.EXPOSE,
        "carry_signal": Kind.FACADE,
        "export_positions": Kind.FACADE,
        "daily_prices": Kind.FACADE,
        "risk_model_portfolio_var": Kind.SERVICE,
    }
    assert entries["annualised_volatility"][1] == ["typed, documented, and pure"]
    assert entries["carry_signal"][1] == [
        "no type hint: lookback, prices, return",
        "no docstring",
        "pandas data: prices",
        "reads module settings: CONFIG['cap'], CONFIG['lookback']",
        "prints",
    ]
    assert "writes files: .to_csv()" in entries["export_positions"][1]
    assert "returns nothing; a tool must return a result" in entries["export_positions"][1]
    assert entries["risk_model_portfolio_var"][1][1:] == [
        "a method of RiskModel, whose constructor sets state",
        "slow set-up: time.sleep",
        "caches: self._cache",
    ]


def test_the_scan_never_imports_the_code() -> None:
    # firm_legacy.warehouse raises on import; the scan still reads it, and the autouse fixture
    # checks that nothing of firm_legacy was imported.
    entries = _entries()

    assert "opens or uses a database" in entries["daily_prices"][1][0]


def test_permissions_and_data_are_drafted_from_what_was_seen() -> None:
    inventory = scan(FIXTURE)
    entries = {entry.name: entry for entry in inventory.entries}

    assert entries["export_positions"].permissions == [Permission.WRITE_LOCAL_DATA]
    assert entries["daily_prices"].permissions == [Permission.READ_LOCAL_DATA]  # SQLite: local
    assert entries["carry_signal"].permissions == []
    assert entries["risk_model_portfolio_var"].permissions == []  # call_service adds network
    assert entries["carry_signal"].data == {"prices": "series"}
    assert entries["export_positions"].data == {"positions": "frame"}
    assert entries["cross_section"].data == {"prices": "frame"}
    assert entries["annualised_volatility"].data == {"returns": "series"}


def _module(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(text, "utf-8")
    return path


def test_a_helper_passes_its_behaviour_to_its_callers(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "desk",
        "import os\nimport requests\n\n"
        "def _save(frame, path):\n    frame.to_parquet(path)\n\n"
        "def _token():\n    return os.environ['DESK_TOKEN']\n\n"
        "def publish(frame: list[float], path: str) -> str:\n"
        '    """Publish a table."""\n'
        "    _save(frame, path)\n"
        "    requests.post('https://desk.example', headers={'t': _token()})\n"
        "    return path\n",
    )
    (entry,) = scan(path).entries
    found = scan(path).found[entry.target]

    assert entry.kind is Kind.FACADE
    assert found.traits[Trait.WRITES_FILES] == (".to_parquet()",)
    assert found.traits[Trait.NETWORK] == ("requests.post",)
    assert found.traits[Trait.ENV] == ("os.environ",)
    assert entry.permissions == [Permission.NETWORK, Permission.WRITE_LOCAL_DATA]


def test_module_state_makes_a_service_and_all_limits_what_is_public(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "cache",
        "__all__ = ['lookup']\n_SEEN = {}\n\n"
        "def lookup(key: str) -> int:\n"
        '    """Look a key up, remembering it."""\n'
        "    _SEEN[key] = len(key)\n    return _SEEN[key]\n\n"
        "def hidden(x: int) -> int:\n"
        '    """Not in __all__."""\n'
        "    return x\n",
    )
    (entry,) = scan(path).entries

    assert entry.name == "lookup" and entry.kind is Kind.SERVICE


def test_unsupported_types_and_var_args_need_a_facade(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "odd",
        "from decimal import Decimal\n\n"
        "def total(*amounts: float, rounding: Decimal = Decimal(2)) -> float:\n"
        '    """Add amounts."""\n'
        "    return sum(amounts)\n",
    )
    (entry,) = scan(path).entries
    reasons = classify(scan(path).found[entry.target])[1]

    assert entry.kind is Kind.FACADE
    assert "takes *amounts; a tool names each parameter" in reasons
    assert "a type a client cannot send or read: rounding: Decimal" in reasons


def test_a_file_that_does_not_parse_is_skipped_with_the_reason(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "good.py").write_text('def f(x: int) -> int:\n    """Same."""\n    return x\n')
    (package / "bad.py").write_text("def broken(:\n")
    (package / "test_good.py").write_text("def test_f() -> None:\n    pass\n")
    (package / "_private.py").write_text("def g() -> int:\n    return 1\n")

    inventory = scan(package)

    assert [entry.target for entry in inventory.entries] == ["pkg.good.f"]
    assert inventory.skipped == ["pkg.bad: cannot parse line 1: invalid syntax"]


def test_nothing_to_scan_is_an_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(LegacyError, match="does not exist"):
        scan(tmp_path / "missing")
    with pytest.raises(LegacyError, match="No Python modules"):
        scan(tmp_path)


def test_the_inventory_round_trips_and_keeps_the_developers_edits(tmp_path: Path) -> None:
    inventory = scan(FIXTURE)
    path = tmp_path / "legacy.toml"
    write_inventory(inventory, path)

    loaded = load_inventory(path)

    assert loaded.source == FIXTURE.resolve()
    assert loaded.entries == inventory.entries
    assert "# carry_signal(prices, lookback=None)" in path.read_text()
    with pytest.raises(LegacyError, match="--force"):
        write_inventory(inventory, path)
    write_inventory(inventory, path, force=True)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('kind = "facade"', 'kind = "plugin"'), "kind must be expose, facade, or service"),
        (("permissions = []", 'permissions = ["root"]'), "permissions are read_local_data"),
        (('data = { prices = "series" }', 'data = { prices = "table" }'), '"series" or "frame"'),
        (('name = "cross_section"', 'name = "carry_signal"'), "more than one included entry"),
    ],
)
def test_a_bad_inventory_says_what_to_fix(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    path = tmp_path / "legacy.toml"
    text = dump_inventory(scan(FIXTURE), path)
    path.write_text(text.replace(*change, 1), "utf-8")

    with pytest.raises(LegacyError, match=message):
        load_inventory(path)


def test_the_command_prints_a_table_json_and_writes_an_inventory(tmp_path: Path) -> None:
    runner = CliRunner()
    table = runner.invoke(cli, ["legacy", "scan", str(FIXTURE)])
    as_json = runner.invoke(cli, ["legacy", "scan", str(FIXTURE), "--json"])
    written = runner.invoke(
        cli, ["legacy", "scan", str(FIXTURE), "--write", str(tmp_path / "inv.toml")]
    )
    again = runner.invoke(
        cli, ["legacy", "scan", str(FIXTURE), "--write", str(tmp_path / "inv.toml")]
    )

    assert table.exit_code == 0, table.output
    assert "2 expose, 3 facade, 1 service" in table.output
    document = json.loads(as_json.output)
    carry = next(e for e in document["entries"] if e["name"] == "carry_signal")
    assert carry["kind"] == "facade" and carry["documented"] is False
    assert carry["traits"]["prints"] == ["print"]
    assert carry["params"][0] == {
        "name": "prices",
        "annotation": None,
        "default": None,
        "kind": "positional",
        "data": "series",
        "library": "pandas",
    }
    assert written.exit_code == 0 and (tmp_path / "inv.toml").exists()
    assert again.exit_code == 1 and "--force" in again.output
