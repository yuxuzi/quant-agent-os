"""Getting started: `qaos init` makes the home, `connect` a client's config, `doctor` the checks."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from quant_agent_os.api import ApiKeyError, load_keys
from quant_agent_os.config import get_settings, home_dir
from quant_agent_os.data import DatasetError, DirectoryDatasetStore
from quant_agent_os.tools import READ_ONLY
from quant_platform.cli_common import console, errors
from quant_platform.composition import DEFAULT_TOOLSETS, available_toolsets
from quant_research import open_research
from quant_research.infrastructure.postgres_store import is_postgres_url
from quant_research.infrastructure.sqlite_store import StoreError

SAMPLE = Path(__file__).parent / "sample"
SERVER_NAME = "quant-agent-os"
# Each extra, the module that shows it is installed, and what is lost without it.
EXTRAS = {
    "server": ("mcp", "`qaos serve` cannot run"),
    "analytics": ("empyrical", "no metrics tools"),
    "pdf": ("pypdf", "PDFs are skipped at ingestion"),
    "parquet": ("pyarrow", "`qaos data add` takes CSV only"),
    "postgres": ("psycopg", "SQLite stores only"),
    "factory": ("pytemplate", "`qaos app` cannot run"),
}


class Client(StrEnum):
    """Clients `qaos connect` knows how to configure."""

    CLAUDE = "claude"
    CURSOR = "cursor"
    VSCODE = "vscode"


def client_config_path(client: Client) -> Path:
    """Return where ``client`` reads a project's MCP configuration, relative to the project."""
    return {
        Client.CLAUDE: Path(".mcp.json"),
        Client.CURSOR: Path(".cursor") / "mcp.json",
        Client.VSCODE: Path(".vscode") / "mcp.json",
    }[client]


def server_entry(client: Client, command: str = "qaos", home: str | None = None) -> dict[str, Any]:
    """Return the MCP server entry for ``client``, with ``home`` if it is not the default."""
    entry: dict[str, Any] = {"command": command, "args": ["serve"]}
    if client is Client.VSCODE:
        entry = {"type": "stdio", **entry}
    if home:
        entry["env"] = {"QUANT_AGENT_OS_HOME": home}
    return entry


def _servers_key(client: Client) -> str:
    return "servers" if client is Client.VSCODE else "mcpServers"


def connect(
    client: Annotated[Client, typer.Argument(help="The client to configure.")],
    write: Annotated[
        bool, typer.Option("--write", help="Write the project's config file, not just print it.")
    ] = False,
    project: Annotated[
        Path, typer.Option("--project", help="The project directory that holds the config file.")
    ] = Path("."),
) -> None:
    """Print a client's MCP configuration for this server, or write it with --write.

    Writing keeps the file's other servers and replaces only this one. Clients that start
    servers with a short PATH need the full command path, so that is what it uses.
    """
    command = shutil.which("qaos") or "qaos"
    key = _servers_key(client)
    entry = server_entry(client, command, os.environ.get("QUANT_AGENT_OS_HOME"))
    if not write:
        typer.echo(json.dumps({key: {SERVER_NAME: entry}}, indent=2))
        errors.print(f"Put this in {client_config_path(client)}, or run again with --write.")
        return
    path = project / client_config_path(client)
    try:
        config = json.loads(path.read_text("utf-8")) if path.exists() else {}
        if not isinstance(config, dict) or not isinstance(config.get(key, {}), dict):
            raise ValueError("expected a JSON object")
    except (OSError, ValueError) as error:
        errors.print(
            f"[red]error:[/red] cannot update {path}: {escape(str(error))}. Nothing was changed."
        )
        raise typer.Exit(1) from error
    config.setdefault(key, {})[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", "utf-8")
    console.print(f"Wrote {SERVER_NAME} to {path}. Restart {client.value} to use it.")


def init(
    sample: Annotated[
        bool, typer.Option("--sample", help="Also load a small sample corpus and dataset.")
    ] = False,
) -> None:
    """Create the home, and with --sample load a sample to try. An existing store is kept."""
    settings = get_settings()
    settings.datasets_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"Home: {home_dir()}")
    if sample:
        if is_postgres_url(settings.store) or Path(settings.store).exists():
            console.print("A knowledge store exists, so the sample corpus was not added to it.")
        else:
            with open_research(settings.store) as research:
                result = research.ingest.ingest(SAMPLE / "corpus")
            console.print(f"Sample corpus: {result.chunks} chunks in {settings.store}.")
        store = DirectoryDatasetStore(settings.datasets_dir)
        if "monthly-returns" in store.ids():
            console.print("A dataset named monthly-returns exists, so it was kept.")
        else:
            store.put("monthly-returns", SAMPLE / "datasets" / "monthly-returns.csv")
            console.print("Sample dataset: monthly-returns.")
    console.print(
        "Next: `qaos connect claude` (or cursor, vscode); `qaos doctor` checks the setup."
    )


def doctor() -> None:
    """Check the home, store, datasets, extras, and permissions, and say how to fix what is off."""
    settings = get_settings()
    rows: list[tuple[str, str, str]] = []

    def add(check: str, status: str, detail: str) -> None:
        rows.append((check, status, detail))

    home = home_dir()
    if home.is_dir():
        add("home", "ok", str(home))
    else:
        add("home", "warn", f"{home} does not exist; run `qaos init`.")

    summary = settings.public_summary()
    if is_postgres_url(settings.store) or Path(settings.store).is_file():
        try:
            with open_research(settings.store, read_only=True):
                add("knowledge store", "ok", summary["store"])
        except StoreError as error:
            add("knowledge store", "fail", str(error))
    else:
        add(
            "knowledge store",
            "warn",
            f"none at {summary['store']}; run `qaos ingest DIR` or `qaos init --sample`.",
        )

    try:
        ids = DirectoryDatasetStore(settings.datasets_dir).ids()
        if ids:
            add("datasets", "ok", f"{len(ids)} in {settings.datasets_dir}")
        else:
            add("datasets", "warn", f"none in {settings.datasets_dir}; run `qaos data add FILE`.")
    except (DatasetError, OSError) as error:
        add("datasets", "fail", str(error))

    add("run store", "ok", str(settings.runs_file))

    if settings.api_keys_file.is_file():
        try:
            keys = load_keys(settings.api_keys_file)
            expired = sum(key.expired for key in keys)
            shown = f"{len(keys)} configured" + (f", {expired} expired" if expired else "")
            add("API keys", "ok", shown)
        except ApiKeyError as error:
            add("API keys", "fail", str(error))
    else:
        add("API keys", "ok", "none; only `qaos serve --http` needs them (`qaos keys new`)")

    for extra, (module, without) in EXTRAS.items():
        if importlib.util.find_spec(module) is not None:
            add(f"extra: {extra}", "ok", "installed")
        else:
            status = "fail" if extra == "server" else "info"
            add(f"extra: {extra}", status, f"not installed: {without}")
    served = [name for name in DEFAULT_TOOLSETS if name in available_toolsets()]
    add("default toolsets", "ok", ", ".join(served))

    wider = sorted(p.value for p in settings.granted_permissions - READ_ONLY)
    if wider:
        add("permissions", "warn", f"wider than read-only: {', '.join(wider)}")
    else:
        add("permissions", "ok", "read-only")
    if settings.preapproved_permissions:
        names = ", ".join(sorted(p.value for p in settings.preapproved_permissions))
        add("pre-approved", "warn", f"run without an approval: {names}")

    for old, new in (
        (Path(".quant-research") / "knowledge.db", "knowledge.db"),
        (home / "factory-runs.db", "runs.db"),
    ):
        if old.is_file():
            add("old location", "warn", f"{old} is no longer read; the home uses {home / new}.")

    colours = {"ok": "green", "info": "cyan", "warn": "yellow", "fail": "red"}
    table = Table(title="qaos doctor")
    for column in ("Check", "Status", "Detail"):
        table.add_column(column, overflow="fold")
    for check, status, detail in rows:
        table.add_row(check, f"[{colours[status]}]{status}[/{colours[status]}]", escape(detail))
    console.print(table)
    if any(status == "fail" for _, status, _ in rows):
        raise typer.Exit(1)
