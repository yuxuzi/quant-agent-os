"""Serve a `Catalog`: what `qaos serve` runs, and what a firm's own server calls (decision 0020)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from quant_agent_os.api import ApiKeyError, KeyRing
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.config import get_settings
from quant_agent_os.data import DatasetStore
from quant_agent_os.mcp import run_stdio
from quant_agent_os.registration import FirmApp
from quant_agent_os.runs import RunRecorder, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tools import (
    AccessPolicy,
    FileAccessPolicy,
    PermissionPolicy,
    ToolRegistry,
    ToolSpec,
)
from quant_platform.composition import Catalog, compose
from quant_platform.http import build_http_app, single_tenant
from quant_research.application.retrieval import ResearchService

errors = Console(stderr=True, soft_wrap=True)


def warn_about_open_access(catalog: Catalog, ring: KeyRing) -> None:
    """Warn when an operator seems to expect per-caller access that this server will not apply.

    Reads the keys ``ring`` already holds, so a keys file that changes meanwhile cannot stop
    the server from starting.
    """
    configured = get_settings().access_file
    if catalog.access is None:
        if configured is not None:
            errors.print(
                "[yellow]warning:[/yellow] an access file is configured, but this catalog has no "
                "access policy, so every key reaches every tool and dataset. "
                "Pass compose(access=...)."
            )
        elif any(key.groups for key in ring.enabled()):
            errors.print(
                "[yellow]warning:[/yellow] some keys name groups, but there is no access file, so "
                "groups do nothing and every key reaches every tool and dataset. Use --access."
            )
    elif configured is not None and not _applies(catalog.access, configured):
        errors.print(
            f"[yellow]warning:[/yellow] the configured access file {configured} is not this "
            "catalog's access policy, so its rules do not apply; the catalog's policy decides. "
            "Pass compose(access=FileAccessPolicy(...)) for that file, or unset "
            "QUANT_AGENT_OS_ACCESS_FILE."
        )


def _applies(access: AccessPolicy, path: Path) -> bool:
    """Return whether ``access`` is the rules of the access file at ``path``."""
    return isinstance(access, FileAccessPolicy) and access.path.resolve() == path.resolve()


def warn_about_permissions(tools: list[ToolSpec[Any, Any]], policy: PermissionPolicy) -> None:
    """Warn about tools the policy will deny, or will hold for an approval on every call."""
    for spec in tools:
        missing = sorted(p.value for p in spec.permissions - policy.granted)
        unapproved = sorted(p.value for p in policy.needs_approval(spec.permissions))
        if missing:
            errors.print(
                f"[yellow]warning:[/yellow] {spec.name} needs {', '.join(missing)}, which is "
                "not granted, so calls will be denied. Grant it with "
                "QUANT_AGENT_OS_GRANTED_PERMISSIONS, or do not serve it."
            )
        elif unapproved:
            errors.print(
                f"[yellow]warning:[/yellow] {spec.name} needs {', '.join(unapproved)}, which "
                "needs an approval for every call, so calls will return approval_required. "
                "Pre-approve it with QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS, or do not serve it."
            )


def serve_stdio(catalog: Catalog, *, runs: Path | None = None, record: bool = True) -> None:
    """Serve ``catalog`` over stdio until the client disconnects; raises `MCPUnavailableError`.

    Stdout carries the protocol, so messages go to stderr. A run store that will not open is a
    warning here, because a client that launches the server cannot show an error. Raises
    `ValueError` for a catalog with an access policy: stdio has no authenticated caller.
    """
    if catalog.access is not None:
        raise ValueError("An access policy needs serve_http: stdio has no authenticated caller.")
    run_store: SqliteRunStore | None = None
    try:
        if record:
            try:
                run_store = SqliteRunStore(runs or get_settings().runs_file)
            except RunStoreError as error:
                errors.print(f"[yellow]warning:[/yellow] {error} Calls will not be recorded.")
        warn_about_permissions(catalog.tools, catalog.policy)
        run_stdio(
            ToolExecutor(ToolRegistry(catalog.tools), catalog.policy),
            name="quant-agent-os",
            instructions=catalog.instructions,
            recorder=(
                RunRecorder(run_store, actor="mcp", toolsets=catalog.toolsets)
                if run_store
                else None
            ),
            resources=catalog.resources,
            prompts=catalog.prompts,
        )
    finally:
        if run_store is not None:
            run_store.close()


def serve_http(
    catalog: Catalog,
    *,
    keys: Path | None = None,
    runs: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allowed_hosts: Sequence[str] = (),
) -> None:
    """Serve ``catalog`` as MCP at /mcp and REST at /v1 until interrupted (decision 0012).

    Every call needs an API key and is recorded with its principal. ``keys`` and ``runs`` default
    to the configured files, and ``allowed_hosts`` are public names clients use. Binds loopback
    by default; put TLS and rate limiting in a reverse proxy before binding anything else.
    Raises `ApiKeyError` without an enabled key, `RunStoreError` without a run store, and
    `ApiUnavailableError` or `MCPUnavailableError` without the ``server`` extra.
    """
    settings = get_settings()
    keys_file = keys or settings.api_keys_file
    ring = KeyRing.from_file(keys_file)
    if not len(ring):
        raise ApiKeyError("the keys file has no enabled key; add one with `qaos keys new`.")
    run_store = SqliteRunStore(runs or settings.runs_file)
    try:
        warn_about_permissions(catalog.tools, catalog.policy)
        warn_about_open_access(catalog, ring)
        if not is_loopback(host):
            errors.print(
                f"[yellow]warning:[/yellow] binding {host}, which other machines can reach; "
                "serve it behind a reverse proxy that terminates TLS and limits request rates."
            )
        http_app = build_http_app(
            single_tenant(catalog, run_store, catalog.policy),
            ring,
            instructions=catalog.instructions,
            extra_hosts=list(allowed_hosts),
        )
        plural = "" if len(ring) == 1 else "s"
        errors.print(
            f"Serving {len(catalog.tools)} tools at http://{host}:{port}/mcp (MCP) and /v1 (REST) "
            f"to {len(ring)} key{plural}."
        )
        run_http(http_app, host=host, port=port)
    finally:
        run_store.close()


def serve_apps(
    apps: Sequence[FirmApp],
    *,
    datasets: DatasetStore | None = None,
    research: ResearchService | None = None,
    policy: PermissionPolicy | None = None,
    access: AccessPolicy | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Serve a firm's ``apps`` beside the platform's toolsets until interrupted (decision 0024).

    ``datasets`` is the firm's data, read by its apps and by the platform's analytics. The
    permission policy and the access file come from the usual settings unless given, as for
    `qaos serve`, and keys and the run store are `serve_http`'s. Raises as `serve_http` does,
    and `ToolContractError` when an app reads data and no ``datasets`` is given.
    """
    settings = get_settings()
    if access is None and settings.access_file is not None:
        access = FileAccessPolicy(settings.access_file)
    catalog = compose(
        policy=policy or settings.policy(),
        research=research,
        datasets=datasets,
        access=access,
        apps=apps,
    )
    serve_http(catalog, host=host, port=port)
