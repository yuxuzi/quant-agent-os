"""`qaos legacy`: turn a fund's existing Python into a draft of tools, on this machine.

`scan` inventories a package without importing it; `wrap` drafts facades, a server file, a
service for stateful code, and a contract test from the edited inventory. The draft is a first
version a developer runs and finishes (decision 0029); the server never reads or writes code.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quant_app_factory.application.legacy import scan, wrap
from quant_app_factory.domain.legacy import Inventory, LegacyError, classify
from quant_app_factory.infrastructure.legacy_files import write_inventory

app = typer.Typer(
    help="Draft tools from a fund's existing Python: scan it, edit the inventory, then wrap.",
    no_args_is_help=True,
)
console = Console()
errors = Console(stderr=True, soft_wrap=True)


def _fail(error: Exception) -> typer.Exit:
    errors.print(f"[red]error:[/red] {escape(str(error))}")
    return typer.Exit(code=1)


@app.command("scan")
def scan_command(
    path: Annotated[Path, typer.Argument(help="A package directory or a .py file.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the inventory as JSON.")] = False,
    write: Annotated[
        Path | None,
        typer.Option("--write", help="Write an editable inventory (TOML) to this file."),
    ] = None,
    app_name: Annotated[
        str, typer.Option("--app", help="The FirmApp's name in the draft.")
    ] = "legacy",
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing inventory file.")
    ] = False,
) -> None:
    """Inventory public functions and methods, and classify each, without importing the code."""
    try:
        inventory = scan(path, app=app_name)
        if write is not None:
            write_inventory(inventory, write, force=force)
    except LegacyError as error:
        raise _fail(error) from error
    if as_json:
        print(json.dumps(_as_json(inventory), indent=2))
        return
    _print_table(inventory)
    for reason in inventory.skipped:
        errors.print(f"[yellow]skipped:[/yellow] {escape(reason)}")
    if write is not None:
        console.print(
            f"Wrote {escape(str(write))}. Review each entry with the developer, then run "
            f"qaos legacy wrap {escape(str(write))} --out legacy_tools",
            soft_wrap=True,
        )
    else:
        console.print("Write an editable inventory with --write legacy.toml.")


@app.command("wrap")
def wrap_command(
    inventory: Annotated[Path, typer.Argument(help="An inventory written by `qaos legacy scan`.")],
    out: Annotated[Path, typer.Option("--out", help="The directory to draft into.")],
    force: Annotated[bool, typer.Option("--force", help="Overwrite drafted files.")] = False,
) -> None:
    """Draft facades, a server file, a service, and a contract test from an inventory."""
    try:
        wrapped = wrap(inventory, out, force=force)
    except LegacyError as error:
        raise _fail(error) from error
    for path in wrapped.written:
        console.print(f"wrote {escape(str(path))}", soft_wrap=True)
    console.print(
        f"Drafted {len(wrapped.draft.tools)} tools with {len(wrapped.draft.todos)} open "
        "decisions (TODO). This is a draft: it is not expected to pass first time.",
        soft_wrap=True,
    )
    console.print("Next:")
    for number, step in enumerate(wrapped.next_steps, 1):
        console.print(f"  {number}. {escape(step)}", soft_wrap=True)


def _print_table(inventory: Inventory) -> None:
    table = Table(title=f"{inventory.source.name}: {len(inventory.entries)} callables")
    table.add_column("Tool")
    table.add_column("Kind")
    table.add_column("Why")
    for entry in inventory.entries:
        found = inventory.found[entry.target]
        _, reasons = classify(found)
        table.add_row(escape(entry.name), entry.kind.value, escape("; ".join(reasons)))
    console.print(table)
    counts = {kind: sum(e.kind.value == kind for e in inventory.entries) for kind in KINDS}
    console.print(", ".join(f"{count} {kind}" for kind, count in counts.items()))


KINDS = ("expose", "facade", "service")


def _as_json(inventory: Inventory) -> dict[str, Any]:
    entries = []
    for entry in inventory.entries:
        found = inventory.found[entry.target]
        _, reasons = classify(found)
        entries.append(
            {
                "target": entry.target,
                "name": entry.name,
                "kind": entry.kind.value,
                "reasons": reasons,
                "signature": found.signature,
                "params": [asdict(param) for param in found.params],
                "returns": found.returns,
                "documented": found.doc is not None,
                "traits": {trait.value: list(seen) for trait, seen in found.traits.items()},
                "data": entry.data,
                "permissions": [permission.value for permission in entry.permissions],
            }
        )
    return {
        "source": str(inventory.source),
        "app": inventory.app,
        "entries": entries,
        "skipped": inventory.skipped,
    }
