"""Local website bridge hosting a real Quant-Agent-OS Portfolio Intelligence MCP & REST server.

This script starts an unmodified ``examples/portfolio-intelligence/demo.py --serve`` server on
``127.0.0.1:8000`` and exposes a CORS-enabled local bridge on ``127.0.0.1:8010`` so the QAOS
website can run real MCP tool calls, inspect live tool/resource/prompt catalogs, demonstrate
per-caller access refusals, and query the auditor's REST trail without requiring user login.

Usage:
  uv run python website/demo_bridge.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx2
import typer
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "examples" / "portfolio-intelligence" / "demo.py"


def _load_demo_module() -> Any:
    """Import ``examples/portfolio-intelligence/demo.py`` dynamically."""
    demo_dir = str(DEMO_PATH.parent)
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    spec = importlib.util.spec_from_file_location("qaos_pi_demo", DEMO_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load demo module from {DEMO_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["qaos_pi_demo"] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class BridgeState:
    """Runtime coordinates and fictional demo keys for the local MCP server."""

    mcp_base_url: str
    growth_key: str
    value_key: str
    auditor_key: str

    def key_for(self, persona: str) -> str:
        """Return the fictional API key for a caller persona."""
        mapping = {
            "growth-pm": self.growth_key,
            "value-analyst": self.value_key,
            "auditor": self.auditor_key,
        }
        if persona not in mapping:
            raise HTTPException(status_code=400, detail=f"Unknown demo persona: {persona}")
        return mapping[persona]


class CallRequest(BaseModel):
    """Tool invocation request forwarded from the local website."""

    persona: str = Field(default="growth-pm")
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _wait_healthy(url: str, proc: subprocess.Popen[bytes], log_path: Path) -> None:
    """Poll ``/healthz`` until the local QAOS server answers or exits."""
    for _ in range(300):
        if proc.poll() is not None:
            break
        try:
            if httpx2.get(f"{url}/healthz", timeout=2).status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.1)
    log_text = log_path.read_text("utf-8") if log_path.exists() else "(no log)"
    raise RuntimeError(f"Demo MCP server failed to start:\n{log_text}")


def create_bridge_app(state: BridgeState) -> FastAPI:
    """Build the CORS-enabled FastAPI bridge forwarding to the real local QAOS server."""
    app = FastAPI(title="QAOS Website Local MCP Bridge", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Verify upstream QAOS server readiness and return local connection info."""
        async with httpx2.AsyncClient(timeout=5) as client:
            upstream = await client.get(f"{state.mcp_base_url}/readyz")
        return {
            "status": "ok" if upstream.status_code == 200 else "degraded",
            "upstream_readyz": upstream.json(),
            "mode": "live-mcp",
            "mcp_url": f"{state.mcp_base_url}/mcp",
            "rest_url": f"{state.mcp_base_url}/v1",
            "demo_keys": {
                "growth-pm": state.growth_key,
                "value-analyst": state.value_key,
                "auditor": state.auditor_key,
            },
        }

    @app.get("/catalog")
    async def catalog(persona: str = "growth-pm") -> dict[str, Any]:
        """Inspect live MCP tools, resources, prompts, and datasets for ``persona``."""
        if persona == "auditor":
            return {
                "persona": persona,
                "tools": [],
                "resources": [],
                "prompts": [],
                "datasets": [],
                "note": "Auditor key holds runs:read scope only; no MCP tool access.",
            }
        key = state.key_for(persona)
        headers = {"Authorization": f"Bearer {key}"}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=30) as client,
            streamable_http_client(f"{state.mcp_base_url}/mcp", http_client=client) as (
                read,
                write,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools_res = await session.list_tools()
            resources_res = await session.list_resources()
            prompts_res = await session.list_prompts()
            datasets_payload: list[dict[str, Any]] = []
            if any(t.name == "list_datasets" for t in tools_res.tools):
                ds_res = await session.call_tool("list_datasets", {})
                if ds_res.structured_content:
                    datasets_payload = list(ds_res.structured_content.get("datasets", []))
            return {
                "persona": persona,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema,
                    }
                    for t in tools_res.tools
                ],
                "resources": [
                    {
                        "uri": str(r.uri),
                        "name": r.name,
                        "description": r.description or "",
                        "mimeType": r.mimeType,
                    }
                    for r in resources_res.resources
                ],
                "prompts": [
                    {
                        "name": p.name,
                        "description": p.description or "",
                        "arguments": [
                            {
                                "name": a.name,
                                "description": a.description or "",
                                "required": bool(a.required),
                            }
                            for a in (p.arguments or [])
                        ],
                    }
                    for p in prompts_res.prompts
                ],
                "datasets": datasets_payload,
            }

    @app.post("/call")
    async def call_tool(req: CallRequest) -> dict[str, Any]:
        """Execute a tool over real Streamable HTTP MCP as ``req.persona``."""
        key = state.key_for(req.persona)
        headers = {"Authorization": f"Bearer {key}"}
        started = time.perf_counter()
        try:
            async with (
                httpx2.AsyncClient(headers=headers, timeout=30) as client,
                streamable_http_client(f"{state.mcp_base_url}/mcp", http_client=client) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(req.tool, req.arguments)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "persona": req.persona,
                "tool": req.tool,
                "duration_ms": elapsed_ms,
                "is_error": True,
                "result": {"status": "denied", "error": str(exc)},
                "text": str(exc),
            }
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        text_parts = [
            getattr(block, "text", "") for block in result.content if getattr(block, "text", None)
        ]
        return {
            "persona": req.persona,
            "tool": req.tool,
            "duration_ms": elapsed_ms,
            "is_error": bool(result.is_error),
            "result": dict(result.structured_content or {}),
            "text": "\n".join(text_parts),
        }

    @app.get("/runs")
    async def runs(limit: int = 25) -> dict[str, Any]:
        """Read the append-only audit trail using the fictional auditor key."""
        headers = {"Authorization": f"Bearer {state.auditor_key}"}
        async with httpx2.AsyncClient(
            base_url=state.mcp_base_url, headers=headers, timeout=30
        ) as client:
            runs_list = (
                (await client.get("/v1/runs", params={"limit": limit})).raise_for_status().json()
            )
            calls: list[dict[str, Any]] = []
            for item in runs_list:
                detail = (
                    (await client.get(f"/v1/runs/{item['run_id']}")).raise_for_status().json()
                )
                principal = detail["run"]["principal"]
                for call in detail["calls"]:
                    calls.append(
                        {
                            "run_id": item["run_id"],
                            "principal": principal,
                            "tool": call["tool"],
                            "tool_version": call["tool_version"],
                            "status": call["status"],
                            "input_sha256": call["input_sha256"],
                            "data_versions": call["data_versions"],
                            "refused": call["refused"],
                            "started_at": call["started_at"],
                        }
                    )
        calls.sort(key=lambda c: c["started_at"], reverse=True)
        return {"count": len(calls), "calls": calls}

    return app


def run_bridge(
    mcp_port: Annotated[int, typer.Option(help="Port for the real QAOS MCP & REST server.")] = 8000,
    bridge_port: Annotated[int, typer.Option(help="Port for the CORS demo bridge.")] = 8010,
) -> None:
    """Prepare demo data, launch the QAOS server, and run the local website bridge."""
    demo = _load_demo_module()
    with tempfile.TemporaryDirectory(prefix="qaos-web-demo-", ignore_cleanup_errors=True) as tmp:
        home = Path(tmp)
        keys = demo.prepare(home)
        mcp_url = f"http://127.0.0.1:{mcp_port}"
        log_path = home / "mcp-server.log"
        cmd = [
            sys.executable,
            str(DEMO_PATH),
            "--serve",
            "--home",
            str(home),
            "--port",
            str(mcp_port),
        ]
        with log_path.open("wb") as log_file:
            server = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
            try:
                _wait_healthy(mcp_url, server, log_path)
                print(demo.connect_instructions(keys, mcp_port), flush=True)
                print(
                    f"Website Demo Bridge listening at http://127.0.0.1:{bridge_port}",
                    flush=True,
                )
                state = BridgeState(
                    mcp_base_url=mcp_url,
                    growth_key=keys.growth,
                    value_key=keys.value,
                    auditor_key=keys.auditor,
                )
                app = create_bridge_app(state)

                @asynccontextmanager
                async def _noop_lifespan(_app: FastAPI) -> Any:
                    yield

                app.router.lifespan_context = _noop_lifespan
                uvicorn.run(app, host="127.0.0.1", port=bridge_port, log_level="warning")
            finally:
                server.terminate()
                server.wait(timeout=15)


if __name__ == "__main__":
    typer.run(run_bridge)
