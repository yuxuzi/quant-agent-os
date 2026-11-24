"""Serve a `Catalog`: what `qaos serve` runs, and what a firm's own server calls (decision 0020).

`build_server_app` gives the HTTP app a firm's server would serve, without serving it, so a firm
can test its server in process with Starlette's `TestClient`; `serve_apps` serves that same app.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr, ValidationError
from rich.console import Console

from quant_agent_os.api import ApiKeyError, KeyRing
from quant_agent_os.api.server import DEFAULT_HOST, DEFAULT_PORT, is_loopback, run_http
from quant_agent_os.config import SecretConfigError, get_settings, read_secret
from quant_agent_os.data import DatasetStore
from quant_agent_os.mcp import run_stdio
from quant_agent_os.observability import configure_logging
from quant_agent_os.registration import FirmApp
from quant_agent_os.runs import RunRecorder, RunStore, RunStoreError, SqliteRunStore
from quant_agent_os.runtime import ToolExecutor
from quant_agent_os.tenancy import DEFAULT_TENANT
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

if TYPE_CHECKING:
    from fastapi import FastAPI

    from quant_platform.identity.sign_in import SignIn

errors = Console(stderr=True, soft_wrap=True)
log = logging.getLogger(__name__)


def known_secrets() -> list[str]:
    """Return the secret values this installation holds, for the log filter to redact.

    A secret that cannot be read yields nothing here: the command that needs it reports why.
    """
    from quant_research.config import embedding_settings, ragflow_settings

    values: list[SecretStr | None] = []
    with suppress(SecretConfigError, ValidationError):
        embedding = embedding_settings()
        values += [embedding.voyage_api_key, embedding.openai_api_key]
    with suppress(SecretConfigError, ValidationError):
        values.append(ragflow_settings().api_key)
    with suppress(SecretConfigError):
        password = read_secret("PGPASSWORD")
        if password:
            values.append(SecretStr(password))
    return [value.get_secret_value() for value in values if value is not None]


def warn_about_open_access(catalog: Catalog, ring: KeyRing, sign_in: SignIn | None = None) -> None:
    """Warn when an operator seems to expect per-caller access that this server will not apply.

    Reads the keys ``ring`` already holds, so a keys file that changes meanwhile cannot stop
    the server from starting.
    """
    configured = get_settings().access_file
    if catalog.access is None:
        if sign_in is not None and configured is None:
            errors.print(
                "[yellow]warning:[/yellow] sign-in is on, but there is no access file, so every "
                "user the identity provider signs in for this server reaches every tool and "
                "dataset, and the groups claim does nothing. Use --access."
            )
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
    """Warn about tools the policy will deny, or will hold for an approval on every call.

    Also warn about standing approvals that are wider than the operator may expect: one for
    every tool (decision 0030), or one naming a tool this server does not serve, which approves
    nothing.
    """
    for spec in tools:
        missing = sorted(p.value for p in spec.permissions - policy.granted)
        unapproved = sorted(p.value for p in policy.needs_approval(spec.permissions, spec.name))
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
                "Pre-approve it for this tool with QUANT_AGENT_OS_PREAPPROVED_TOOLS, or do not "
                "serve it."
            )
    if policy.preapproved:
        names = ", ".join(sorted(p.value for p in policy.preapproved))
        errors.print(
            f"[yellow]warning:[/yellow] QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS approves {names} "
            "for every tool this server serves, now and later, without a per-call approval. Name "
            "the tools instead with QUANT_AGENT_OS_PREAPPROVED_TOOLS."
        )
    served = {spec.name: spec for spec in tools}
    for permission, tool_names in sorted(policy.preapproved_tools.items()):
        for name in sorted(tool_names):
            named = served.get(name)
            if named is None:
                errors.print(
                    f"[yellow]warning:[/yellow] QUANT_AGENT_OS_PREAPPROVED_TOOLS approves "
                    f"{permission.value} for {name}, which this server does not serve, so it "
                    "approves nothing. Check the name."
                )
            elif permission not in named.permissions:
                errors.print(
                    f"[yellow]warning:[/yellow] QUANT_AGENT_OS_PREAPPROVED_TOOLS approves "
                    f"{permission.value} for {name}, which does not declare it, so it approves "
                    "nothing. Remove it."
                )


def warm_up(catalog: Catalog) -> None:
    """Load what the catalog's analytics tools import before serving, so no caller waits on it.

    A first metric call on a fresh install compiles the metrics backend, which can outlast a tool
    call's time limit. A failure here is left for the first call to report, as it would have.
    """
    if "analytics" not in catalog.toolsets.values():
        return
    try:
        from quant_analytics.computed import warm_up as warm_analytics

        warm_analytics()
    except Exception:
        log.debug("Analytics warm-up failed", exc_info=True)


def serve_stdio(catalog: Catalog, *, runs: Path | None = None, record: bool = True) -> None:
    """Serve ``catalog`` over stdio until the client disconnects; raises `MCPUnavailableError`.

    Stdout carries the protocol, so messages go to stderr. A run store that will not open is a
    warning here, because a client that launches the server cannot show an error. Raises
    `ValueError` for a catalog with an access policy: stdio has no authenticated caller.
    """
    if catalog.access is not None:
        raise ValueError("An access policy needs serve_http: stdio has no authenticated caller.")
    if get_settings().sign_in_file is not None:
        errors.print(
            "[yellow]warning:[/yellow] a sign-in file is configured, but stdio serves only the "
            "client that launched it, so sign-in does not apply; it applies to `qaos serve --http`."
        )
    run_store: SqliteRunStore | None = None
    try:
        if record:
            try:
                run_store = SqliteRunStore(runs or get_settings().runs_file)
            except RunStoreError as error:
                errors.print(f"[yellow]warning:[/yellow] {error} Calls will not be recorded.")
        warn_about_permissions(catalog.tools, catalog.policy)
        warm_up(catalog)
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
            views=catalog.views,
        )
    finally:
        if run_store is not None:
            run_store.close()


def open_sign_in(path: Path, tenant: str = DEFAULT_TENANT) -> SignIn:
    """Read the sign-in file for a server of ``tenant``; raise `SignInError` naming the problem.

    Every provider's users must act in ``tenant``: a token for any other would reach nothing.
    """
    from quant_platform.identity.sign_in import SignInError, load_sign_in

    sign_in = load_sign_in(path)
    for issuer, tenants in sign_in.tenants_by_issuer.items():
        if tenants != {tenant}:
            raise SignInError(
                f"{path}: this server serves tenant '{tenant}' only, but {issuer}'s users would "
                f'act in {", ".join(sorted(tenants))}. Set tenant = "{tenant}" or remove it.'
            )
    return sign_in


@contextmanager
def _http_app(
    catalog: Catalog,
    *,
    keys: Path | None,
    runs: Path | RunStore | None,
    allowed_hosts: Sequence[str],
    sign_in: Path | None,
) -> Iterator[tuple[FastAPI, str]]:
    """Yield ``catalog``'s HTTP app, and who it serves, with its run store open until the end."""
    settings = get_settings()
    keys_file = keys or settings.api_keys_file
    sign_in_file = sign_in or settings.sign_in_file
    signing_in = open_sign_in(sign_in_file) if sign_in_file is not None else None
    ring = KeyRing.from_file(keys_file, missing_ok=signing_in is not None)
    if not len(ring) and signing_in is None:
        raise ApiKeyError("the keys file has no enabled key; add one with `qaos keys new`.")
    hosts = list(allowed_hosts)
    if signing_in is not None and not is_loopback(signing_in.host):
        hosts.append(signing_in.host)
    owned = runs is None or isinstance(runs, Path)
    run_store: RunStore = (
        SqliteRunStore(runs or settings.runs_file)
        if runs is None or isinstance(runs, Path)
        else runs
    )
    try:
        warn_about_permissions(catalog.tools, catalog.policy)
        warn_about_open_access(catalog, ring, signing_in)
        warm_up(catalog)
        tenancy = single_tenant(catalog, run_store, catalog.policy)
        if signing_in is None:
            http_app = build_http_app(
                tenancy, ring, instructions=catalog.instructions, extra_hosts=hosts
            )
        else:
            from quant_agent_os.mcp.http import KeyVerifier
            from quant_platform.identity.oauth import FirstOf

            routes, metadata_url = signing_in.metadata_routes()
            http_app = build_http_app(
                tenancy,
                ring,
                instructions=catalog.instructions,
                verifier=FirstOf(KeyVerifier(ring), signing_in.verifier()),
                extra_hosts=hosts,
                resource_metadata_url=metadata_url,
                metadata_routes=routes,
            )
        plural = "" if len(ring) == 1 else "s"
        signed_in = (
            f" and to users signed in by {', '.join(signing_in.issuers)}" if signing_in else ""
        )
        yield http_app, f"{len(ring)} key{plural}{signed_in}"
    finally:
        if owned:
            run_store.close()


def serve_http(
    catalog: Catalog,
    *,
    keys: Path | None = None,
    runs: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allowed_hosts: Sequence[str] = (),
    sign_in: Path | None = None,
) -> None:
    """Serve ``catalog`` as MCP at /mcp and REST at /v1 until interrupted (decision 0012).

    Every call needs an API key, or with sign-in (decision 0028) a token from a trusted identity
    provider, and is recorded with its principal. ``keys``, ``runs``, and ``sign_in`` default to
    the configured files, and ``allowed_hosts`` are public names clients use; with sign-in, the
    resource URL's host is one. Binds loopback by default; put TLS and rate limiting in a reverse
    proxy before binding anything else. Logs follow ``QUANT_AGENT_OS_LOG_FORMAT`` and
    ``QUANT_AGENT_OS_LOG_LEVEL``, redacted of the known secrets. Raises `ApiKeyError` without an
    enabled key (unless sign-in is on), `SignInError` for a sign-in file that is invalid or names
    an unset secret, `RunStoreError` without a run store, and `ApiUnavailableError` or
    `MCPUnavailableError` without the ``server`` extra.
    """
    settings = get_settings()
    configure_logging(settings.log_level, secrets=known_secrets(), log_format=settings.log_format)
    with _http_app(catalog, keys=keys, runs=runs, allowed_hosts=allowed_hosts, sign_in=sign_in) as (
        http_app,
        audience,
    ):
        if not is_loopback(host):
            errors.print(
                f"[yellow]warning:[/yellow] binding {host}, which other machines can reach; "
                "serve it behind a reverse proxy that terminates TLS and limits request rates."
            )
        errors.print(
            f"Serving {len(catalog.tools)} tools at http://{host}:{port}/mcp (MCP) and /v1 (REST) "
            f"to {audience}."
        )
        run_http(http_app, host=host, port=port)


def _firm_catalog(
    apps: Sequence[FirmApp],
    datasets: DatasetStore | None,
    research: ResearchService | None,
    policy: PermissionPolicy | None,
    access: AccessPolicy | None,
) -> Catalog:
    settings = get_settings()
    if access is None and settings.access_file is not None:
        access = FileAccessPolicy(settings.access_file)
    return compose(
        policy=policy or settings.policy(),
        research=research,
        datasets=datasets,
        access=access,
        apps=apps,
    )


@contextmanager
def build_server_app(
    apps: Sequence[FirmApp],
    *,
    datasets: DatasetStore | None = None,
    research: ResearchService | None = None,
    policy: PermissionPolicy | None = None,
    access: AccessPolicy | None = None,
    keys: Path | None = None,
    runs: Path | RunStore | None = None,
    sign_in: Path | None = None,
    allowed_hosts: Sequence[str] = (),
) -> Iterator[FastAPI]:
    """Yield the HTTP app `serve_apps` would serve for ``apps``, without serving it.

    The same catalog, keys or sign-in, access, and run store, from the same settings, so a firm
    tests its own server in process::

        with build_server_app([risk], datasets=store) as app, TestClient(app) as client:
            client.post("/v1/tools/desk_var/call", headers=..., json={"arguments": ...})

    ``keys``, ``runs``, and ``sign_in`` default to the configured files. A run store given as a
    path, or the configured one, is opened here and closed when the block ends; one given as a
    store, such as `MemoryRunStore`, is the caller's to close. Raises as `serve_apps` does.
    """
    catalog = _firm_catalog(apps, datasets, research, policy, access)
    with _http_app(catalog, keys=keys, runs=runs, allowed_hosts=allowed_hosts, sign_in=sign_in) as (
        http_app,
        _,
    ):
        yield http_app


def serve_apps(
    apps: Sequence[FirmApp],
    *,
    datasets: DatasetStore | None = None,
    research: ResearchService | None = None,
    policy: PermissionPolicy | None = None,
    access: AccessPolicy | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allowed_hosts: Sequence[str] = (),
) -> None:
    """Serve a firm's ``apps`` beside the platform's toolsets until interrupted (decision 0024).

    ``datasets`` is the firm's data, read by its apps and by the platform's analytics. The
    permission policy and the access file come from the usual settings unless given, as for
    `qaos serve`, and keys, sign-in, and the run store are `serve_http`'s. It serves the app
    `build_server_app` builds. Raises as `serve_http` does, and `ToolContractError` when an app
    reads data and no ``datasets`` is given.
    """
    catalog = _firm_catalog(apps, datasets, research, policy, access)
    serve_http(catalog, host=host, port=port, allowed_hosts=allowed_hosts)
