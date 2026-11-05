"""`qaos init`, `connect`, and `doctor`: from nothing to a configured client."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
    result = RUNNER.invoke(app, ["doctor"], env={"COLUMNS": "200"})

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

    monkeypatch.setattr("quant_platform.cli.run_stdio", missing)
    monkeypatch.setattr("quant_app_factory.cli.open_factory", no_factory)

    assert "quant-agent-os[server]" in RUNNER.invoke(app, ["serve", "--no-record"]).output
    assert "quant-agent-os[factory]" in RUNNER.invoke(app, ["app", "template", "list"]).output
