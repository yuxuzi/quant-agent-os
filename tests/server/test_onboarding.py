"""`qaos init`, `connect`, and `doctor`: from nothing to a configured client."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_agent_os.api import add_key
from quant_agent_os.config import get_settings
from quant_platform.cli import app
from quant_platform.onboarding import Client, client_config_path

RUNNER = CliRunner()


def test_init_sample_gives_a_store_and_a_dataset_that_search_can_use() -> None:
    created = RUNNER.invoke(app, ["init", "--sample"])
    found = RUNNER.invoke(app, ["search", "short-term reversal"])
    listed = RUNNER.invoke(app, ["data", "list"])

    assert created.exit_code == 0, created.output
    assert found.exit_code == 0, found.output
    assert "monthly-returns" in listed.output


def test_init_sample_never_adds_to_an_existing_store(tmp_path: Path) -> None:
    corpus = tmp_path / "mine"
    corpus.mkdir()
    (corpus / "note.md").write_text("Carry correlates with crowding.\n")
    assert RUNNER.invoke(app, ["ingest", str(corpus)]).exit_code == 0

    second = RUNNER.invoke(app, ["init", "--sample"])
    searched = RUNNER.invoke(app, ["search", "short-term reversal", "--json"])

    assert "was not added" in second.output
    assert "reversal-and-microstructure" not in searched.output


def test_ingest_and_serve_agree_on_the_store_without_any_flags(tmp_path: Path) -> None:
    corpus = tmp_path / "mine"
    corpus.mkdir()
    (corpus / "note.md").write_text("Carry correlates with crowding.\n")

    assert RUNNER.invoke(app, ["ingest", str(corpus)]).exit_code == 0
    catalog = RUNNER.invoke(app, ["catalog"], env={"COLUMNS": "200"})

    assert Path(get_settings().store).is_file()
    assert "find_related_signals" in catalog.output


@pytest.mark.parametrize("client", list(Client))
def test_connect_prints_by_default_and_writes_nothing(
    client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    shown = RUNNER.invoke(app, ["connect", client.value])

    assert shown.exit_code == 0
    assert "quant-agent-os" in shown.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("client", "key"),
    [(Client.CLAUDE, "mcpServers"), (Client.CURSOR, "mcpServers"), (Client.VSCODE, "servers")],
)
def test_connect_write_keeps_the_files_other_servers(
    client: Client, key: str, tmp_path: Path
) -> None:
    path = tmp_path / client_config_path(client)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({key: {"other": {"command": "x"}}}))

    written = RUNNER.invoke(app, ["connect", client.value, "--write", "--project", str(tmp_path)])

    servers = json.loads(path.read_text())[key]
    assert written.exit_code == 0, written.output
    assert servers["other"] == {"command": "x"}
    assert servers["quant-agent-os"]["args"] == ["serve"]


def test_connect_write_refuses_a_broken_file_and_leaves_it(tmp_path: Path) -> None:
    path = tmp_path / ".mcp.json"
    path.write_text("{ not json")

    written = RUNNER.invoke(app, ["connect", "claude", "--write", "--project", str(tmp_path)])

    assert written.exit_code == 1
    assert path.read_text() == "{ not json"


def test_doctor_explains_an_empty_home_and_still_passes() -> None:
    # Wide enough that no hint wraps, however long the temporary home's path is.
    result = RUNNER.invoke(app, ["doctor"], env={"COLUMNS": "1000"})

    assert result.exit_code == 0, result.output
    for hint in ("qaos init", "qaos ingest DIR", "qaos data add FILE"):
        assert hint in result.output


def test_doctor_fails_without_the_server_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "mcp" else real(name, *a)
    )

    assert RUNNER.invoke(app, ["doctor"]).exit_code == 1


def test_doctor_flags_permissions_wider_than_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANT_AGENT_OS_GRANTED_PERMISSIONS", '["read_local_data", "network"]')
    get_settings.cache_clear()

    result = RUNNER.invoke(app, ["doctor"], env={"COLUMNS": "200"})

    assert "wider than read-only: network" in result.output


def _doctor_output() -> tuple[int, str]:
    get_settings.cache_clear()
    result = RUNNER.invoke(app, ["doctor"], env={"COLUMNS": "1000"})
    return result.exit_code, " ".join(result.output.split())


def test_doctor_reports_a_run_store_the_server_would_refuse() -> None:
    runs = get_settings().runs_file
    runs.parent.mkdir(parents=True)
    runs.write_text("")  # SQLite reads an empty file as a new database
    db = sqlite3.connect(runs)
    db.execute("PRAGMA user_version = 99")
    db.close()

    code, output = _doctor_output()

    assert code == 1
    assert "run-store schema 99" in output
    db = sqlite3.connect(runs)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 99
    db.close()


def test_doctor_reports_an_unreadable_run_store() -> None:
    runs = get_settings().runs_file
    runs.parent.mkdir(parents=True)
    runs.write_text("this is not a database, and it is long enough to be read as one" * 100)

    code, output = _doctor_output()

    assert code == 1
    assert "Cannot open the run store" in output


def test_doctor_checks_an_access_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    access = tmp_path / "access.toml"
    access.write_text('[groups.desk]\ntools = ["analyze_risk"]\n')
    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(access))

    code, output = _doctor_output()
    assert code == 0
    assert "groups: 1" in output

    access.write_text("not toml =")
    code, output = _doctor_output()
    assert code == 1
    assert "Invalid access file" in output


def test_doctor_warns_when_groups_have_no_access_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keys = get_settings().api_keys_file
    keys.parent.mkdir(parents=True)
    add_key(keys, "alice", ["tools:call"], groups=["desk"])

    assert "no access file applies them" in _doctor_output()[1]

    access = tmp_path / "access.toml"
    access.write_text("[groups.desk]\n")
    monkeypatch.setenv("QUANT_AGENT_OS_ACCESS_FILE", str(access))
    assert "no access file applies them" not in _doctor_output()[1]


def test_doctor_output_holds_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its output goes in support tickets. Each setting here is wrong, so each error is shown."""
    password = "s3cret-store-password"
    monkeypatch.setenv("QUANT_AGENT_OS_STORE", f"postgresql://reader:{password}@127.0.0.1:1/qaos")
    monkeypatch.setenv("PGPASSWORD", "s3cret-pgpassword")
    monkeypatch.setenv("VOYAGE_API_KEY", "s3cret-voyage")
    monkeypatch.setenv("OPENAI_API_KEY", "s3cret-openai")
    keys = get_settings().api_keys_file
    keys.parent.mkdir(parents=True)
    key = add_key(keys, "alice", ["tools:call"])
    digest = hashlib.sha256(key.encode()).hexdigest()

    code, listed = _doctor_output()
    assert "1 configured" in listed
    assert code == 1  # the store cannot be reached

    keys.write_text(keys.read_text("utf-8").replace("principal", "princpal"))
    _, invalid = _doctor_output()
    assert "Invalid API keys file" in invalid

    for output in (listed, invalid):
        for secret in (
            password,
            "s3cret-pgpassword",
            "s3cret-voyage",
            "s3cret-openai",
            key,
            digest,
        ):
            assert secret not in output


def test_a_config_file_setting_permissions_stops_every_command() -> None:
    home = Path(get_settings().store).parent
    home.mkdir(parents=True)
    (home / "config.toml").write_text('granted_permissions = ["network"]\n')
    get_settings.cache_clear()

    result = RUNNER.invoke(app, ["doctor"], env={"COLUMNS": "200"})

    assert result.exit_code == 1
    assert "QUANT_AGENT_OS_GRANTED_PERMISSIONS" in " ".join(result.output.split())


def test_serve_options_for_http_need_http() -> None:
    result = RUNNER.invoke(app, ["serve", "--port", "9000"])

    assert result.exit_code == 2 and "need --http" in result.output


@pytest.mark.parametrize("blocked", ["empyrical", "pytemplate", "mcp", "fastapi", "uvicorn"])
def test_the_command_starts_without_any_extra(blocked: str) -> None:
    """A base install must reach `--help` and a clear message, not an ImportError."""
    code = (
        "import sys\n"
        f"sys.modules[{blocked!r}] = None\n"
        "from quant_platform.cli import main\n"
        "sys.argv = ['qaos', '--help']\n"
        "main()\n"
    )

    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert "serve" in done.stdout


def test_a_missing_extra_is_named_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich must not read `[server]` or `[factory]` in a message as markup and drop it."""
    from quant_agent_os.mcp import MCPUnavailableError
    from quant_app_factory.domain.ports import ScaffoldError

    def missing(*_: object, **__: object) -> None:
        raise MCPUnavailableError("MCP support is not installed; quant-agent-os[server].")

    def no_factory() -> None:
        raise ScaffoldError("The App Factory is not installed; quant-agent-os[factory].")

    monkeypatch.setattr("quant_platform.serve.run_stdio", missing)
    monkeypatch.setattr("quant_app_factory.cli.open_factory", no_factory)

    assert "quant-agent-os[server]" in RUNNER.invoke(app, ["serve", "--no-record"]).output
    assert "quant-agent-os[factory]" in RUNNER.invoke(app, ["app", "template", "list"]).output
