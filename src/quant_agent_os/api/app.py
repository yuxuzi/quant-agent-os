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
from quant_agent_os.runtime.executor import CallStatus, ToolExecutor, failure_details
from quant_agent_os.runtime.plan import PlanStep
from quant_agent_os.tools.permissions import PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from fastapi import FastAPI

INSTALL_HINT = "install the API extra: uv add 'quant-agent-os[api]'"
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

    A failure also carries ``hint`` (what to do next) and ``retryable``, as MCP reports it.
    """

    run_id: str | None
    call_id: str
    tool: str
    status: CallStatus
    output: dict[str, Any] | None
    error: str | None
    hint: str | None = None
    retryable: bool | None = None
    data_versions: dict[str, str]


def build_app(
    registry: ToolRegistry,
    keys: KeyRing,
    run_store: RunStore,
    *,
    policy: PermissionPolicy | None = None,
    max_request_bytes: int = MAX_REQUEST_BYTES,
    title: str = "Quant-Agent-OS",
) -> "FastAPI":
    """Return the REST app serving ``registry``'s allowed tools.

    ``run_store`` records every call and is the approval ledger, so an operator can approve a
    call that returned ``approval_required`` through the SDK on the same store. Raises
    `ApiUnavailableError` without the extra and `ValueError` without an enabled key.
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

    executor = ToolExecutor(registry, policy or PermissionPolicy(), approvals=run_store)
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

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/tools")
    def list_tools(key: Annotated[ApiKey, requires("tools:list")]) -> list[dict[str, Any]]:
        return tool_definitions(executor)

    @app.post("/v1/tools/{name}/call", response_model=CallResponse)
    async def call(
        name: str, request: Request, key: Annotated[ApiKey, requires("tools:call")]
    ) -> Any:
        served = [spec.name for spec in registry.specs() if executor.policy.allows_tool(spec.name)]
        if name not in served:
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
        recorder = RunRecorder(run_store, actor="rest", principal=key.principal, tenant=key.tenant)
        record = await run_in_threadpool(
            recorder.call, executor, PlanStep(tool=name, arguments=arguments)
        )
        failure = failure_details(record, served) if record.status != "ok" else {}
        return CallResponse(
            run_id=record.run_id,
            call_id=record.call_id,
            tool=record.tool,
            status=record.status,
            output=record.output,
            error=record.error,
            hint=failure.get("hint"),
            retryable=failure.get("retryable"),
            data_versions=record.data_versions,
        )

    @app.get("/v1/runs")
    def list_runs(
        key: Annotated[ApiKey, requires("runs:read")], limit: int = 20
    ) -> list[RunRecord]:
        return run_store.list_runs(max(1, min(limit, MAX_RUNS)), tenant=key.tenant)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, key: Annotated[ApiKey, requires("runs:read")]) -> StoredRun:
        stored = run_store.get_run(run_id, tenant=key.tenant)
        if stored is None:
            raise HTTPException(404, f"No run {run_id}.")
        return stored

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
