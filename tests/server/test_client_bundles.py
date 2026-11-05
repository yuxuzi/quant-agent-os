"""Client packaging (plan step P3.3): the Claude Code plugin and the Cursor and VS Code bundles."""

import json
from importlib.metadata import version
from pathlib import Path

import pytest
from client_bundles import PLUGIN, ROOT, SERVER, bundles
from typer.testing import CliRunner

from quant_platform import cli as platform_cli
from quant_research.cli import app as research_cli

CORPUS = ROOT / "src" / "quant_platform" / "sample" / "corpus"


def test_the_client_bundles_are_generated_from_the_current_skills() -> None:
    stale = [
        str(path.relative_to(ROOT))
        for path, content in bundles().items()
        if not path.is_file() or path.read_text("utf-8") != content
    ]

    assert stale == [], "Regenerate with `uv run python scripts/client_bundles.py`"


def test_every_bundle_carries_every_skill_and_the_same_server() -> None:
    for path, content in bundles().items():
        if path.suffix == ".json":
            servers = json.loads(content)
            entry = (servers.get("mcpServers") or servers["servers"])[SERVER]
            assert (entry["command"], entry["args"]) == ("qaos", ["serve"])
        else:
            for skill in (
                "quant-research",
                "quant-analytics",
                "quant-strategy",
                "quant-app-factory",
            ):
                assert f"## {skill}" in content


def test_the_plugin_serves_this_platform_version_with_its_skills() -> None:
    manifest = json.loads(PLUGIN.read_text("utf-8"))
    marketplace = json.loads((PLUGIN.parent / "marketplace.json").read_text("utf-8"))
    args = manifest["mcpServers"][SERVER]["args"]

    assert manifest["version"] == version("quant-agent-os")
    # App Factory is opt-in, so the plugin does not install it.
    assert args[args.index("--extra") :] == [
        "--extra",
        "analytics",
        "--extra",
        "server",
        "qaos",
        "serve",
    ]
    assert "${CLAUDE_PLUGIN_ROOT}" in args and "--locked" in args
    assert [(entry["name"], entry["source"]) for entry in marketplace["plugins"]] == [
        (manifest["name"], "./")
    ]
    assert (ROOT / "skills").is_dir()


def test_without_a_store_the_server_still_starts_without_research(tmp_path: Path) -> None:
    result = CliRunner().invoke(platform_cli.app, ["catalog", "--datasets", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "research toolset is not served" in result.output
    assert "analyze_performance" in result.output and "find_related_signals" not in result.output


def test_choosing_research_without_a_store_is_an_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(platform_cli.app, ["catalog", "--toolsets", "research"])

    assert result.exit_code == 2 and "ingest first" in result.output


def test_the_store_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = tmp_path / "kb.db"
    CliRunner().invoke(research_cli, ["ingest", str(CORPUS), "--store", str(store)])
    monkeypatch.setenv("QUANT_AGENT_OS_STORE", str(store))

    result = CliRunner().invoke(platform_cli.app, ["catalog", "--datasets", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "find_related_signals" in result.output and "not served" not in result.output
