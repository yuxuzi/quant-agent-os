"""The REST interface (decision 0011): FastAPI routes over the executor, with no business logic.

Calls go through the same `ToolExecutor` and `RunRecorder` as MCP, so a REST call returns the
output MCP returns as ``structuredContent``. Every request but ``/healthz`` needs an API key
with the route's scope; each call is recorded as a run with actor ``rest`` and the key's
principal. Needs the optional ``api`` extra.

No ``from __future__ import annotations`` here: FastAPI reads the route annotations at runtime,
and they name objects local to `build_app`.
"""

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quant_agent_os.api.keys import ApiKey, KeyRing, Scope
from quant_agent_os.mcp.adapters import tool_definitions
from quant_agent_os.runs.models import RunRecord, StoredRun
from quant_agent_os.runs.recorder import RunRecorder
from quant_agent_os.runs.store import RunStore
from quant_agent_os.runtime.call import ToolCall
from quant_agent_os.runtime.executor import CallStatus, ToolExecutor, failure_details
from quant_agent_os.serving import SingleTenant, Surface, Tenancy
from quant_agent_os.tools.access import Caller, acting_as
from quant_agent_os.tools.permissions import PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from fastapi import FastAPI

INSTALL_HINT = "install the quant-agent-os[server] extra"
MAX_REQUEST_BYTES = 5 * 1024 * 1024
MAX_RUNS = 100


class ApiUnavailableError(RuntimeError):
    """Raised when FastAPI (the ``api`` extra) is not installed."""


class CallRequest(BaseModel):
    """The body of a tool call."""

    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)


class CallResponse(BaseModel):
    """The record of one call: ``output`` is set when ``status`` is ``ok``, else ``error``.

    A failure also carries ``hint`` (what to do next) and ``retryable``, as MCP reports it. A
    call a usage gate refuses (``quota_exceeded``) is answered 429, and one refused because the
    server is busy 503, with ``retry_after_seconds`` here and in the ``Retry-After`` header.
    """

    run_id: str | None
    call_id: str
    tool: str
    status: CallStatus
    output: dict[str, Any] | None
    error: str | None
    hint: str | None = None
    retryable: bool | None = None
    retry_after_seconds: int | None = None
    data_versions: dict[str, str]


def build_app(
    registry: ToolRegistry | None,
    keys: KeyRing,
    run_store: RunStore | None,
    *,
    policy: PermissionPolicy | None = None,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    title: str = "Quant-Agent-OS",
    tenancy: Tenancy | None = None,
) -> "FastAPI":
    """Return the REST app serving ``registry``'s allowed tools.

    ``run_store`` records every call and is the approval ledger, so an operator can approve a
    call that returned ``approval_required`` through the SDK on the same store. With
    ``tenancy`` (decision 0013), each key is served its own tenant's surface instead, and
    ``registry``, ``run_store``, and ``policy`` are not used. Raises `ApiUnavailableError`
    without the extra and `ValueError` without an enabled key.
    """
    try:
        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    except ImportError as error:
        raise ApiUnavailableError(f"REST support is not installed; {INSTALL_HINT}.") from error
    if not len(keys):
        raise ValueError("The REST API needs at least one enabled API key.")
    if tenancy is None:
        if registry is None or run_store is None:
            raise ValueError("Give a registry and a run store, or a tenancy.")
        executor = ToolExecutor(registry, policy or PermissionPolicy(), approvals=run_store)
        tenancy = SingleTenant(Surface(executor, run_store=run_store))
    served_by: Tenancy = tenancy

    bearer = HTTPBearer(auto_error=False)
    # No unauthenticated schema pages: the tool list itself needs a key.
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)

    def unauthorized(detail: str) -> HTTPException:
        return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})

    def requires(scope: Scope) -> Any:
        def check(
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        ) -> ApiKey:
            if credentials is None:
                raise unauthorized("Send an API key as 'Authorization: Bearer <key>'.")
            key = keys.authenticate(credentials.credentials)
            if key is None:
                raise unauthorized("Unknown or revoked API key.")
            if scope not in key.scopes:
                raise HTTPException(403, f"This API key lacks the '{scope}' scope.")
            return key

        return Depends(check)

    def surface_of(key: ApiKey) -> Surface:
        surface = served_by.surface(key.tenant)
        if surface is None:
            raise HTTPException(403, f"Tenant '{key.tenant}' is not served here.")
        return surface

    def caller_of(key: ApiKey) -> Caller:
        return Caller(key.principal, key.tenant, key.groups)

    def runs_of(key: ApiKey) -> RunStore:
        surface = surface_of(key)
        if surface.run_store is None:
            raise HTTPException(404, "This server keeps no runs.")
        return surface.run_store

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/tools")
    def list_tools(key: Annotated[ApiKey, requires("tools:list")]) -> list[dict[str, Any]]:
        with acting_as(caller_of(key)):
            return tool_definitions(surface_of(key).executor)

    @app.post("/v1/tools/{name}/call", response_model=CallResponse)
    async def call(
        name: str, request: Request, key: Annotated[ApiKey, requires("tools:call")]
    ) -> Any:
        surface = surface_of(key)
        executor = surface.executor
        # A tool this caller may not reach is still a call: it is refused and recorded.
        if name not in executor.registry or not executor.policy.allows_tool(name):
            raise HTTPException(404, f"No tool named '{name}' is served here.")
        body = await _read_body(request, max_request_bytes)
        if body is None:
            return JSONResponse({"detail": "The request body is too large."}, status_code=413)
        try:
            arguments = CallRequest.model_validate_json(body or b"{}").arguments
        except ValidationError:
            raise HTTPException(
                422, 'The body must be a JSON object: {"arguments": {...}}.'
            ) from None
        step = ToolCall(tool=name, arguments=arguments)
        with acting_as(caller_of(key)):
            served = [spec.name for spec in executor.registry.specs() if executor.serves(spec.name)]
            if surface.run_store is None:
                record = await run_in_threadpool(executor.execute, step)
            else:
                recorder = RunRecorder(
                    surface.run_store,
                    actor="rest",
                    principal=key.principal,
                    tenant=key.tenant,
                    toolsets=surface.toolsets,
                )
                record = await run_in_threadpool(recorder.call, executor, step)
        failure = failure_details(record, served) if record.status != "ok" else {}
        response = CallResponse(
            run_id=record.run_id,
            call_id=record.call_id,
            tool=record.tool,
            status=record.status,
            output=record.output,
            error=record.error,
            hint=failure.get("hint"),
            retryable=failure.get("retryable"),
            retry_after_seconds=record.retry_after_seconds,
            data_versions=record.data_versions,
        )
        if record.status == "quota_exceeded":
            return JSONResponse(
                response.model_dump(mode="json"),
                status_code=429,
                headers={"Retry-After": str(record.retry_after_seconds or 1)},
            )
        if record.status == "busy":
            return JSONResponse(
                response.model_dump(mode="json"),
                status_code=503,
                headers={"Retry-After": str(record.retry_after_seconds or 1)},
            )
        return response

    @app.get("/v1/runs")
    def list_runs(
        key: Annotated[ApiKey, requires("runs:read")], limit: int = 20
    ) -> list[RunRecord]:
        return runs_of(key).list_runs(max(1, min(limit, MAX_RUNS)), tenant=key.tenant)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, key: Annotated[ApiKey, requires("runs:read")]) -> StoredRun:
        stored = runs_of(key).get_run(run_id, tenant=key.tenant)
        if stored is None:
            raise HTTPException(404, f"No run {run_id}.")
        # Internal error text is for the operator, not the tenant (security review F-5).
        calls = [call.model_copy(update={"error_detail": None}) for call in stored.calls]
        return stored.model_copy(update={"calls": calls})

    return app


async def _read_body(request: Any, limit: int) -> bytes | None:
    """Return the body, or None once it exceeds ``limit`` bytes (declared or streamed)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)
