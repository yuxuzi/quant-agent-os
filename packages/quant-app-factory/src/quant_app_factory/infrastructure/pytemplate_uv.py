"""`PytemplateUvScaffolder`: the `ScaffoldProvider` backed by the published pytemplate-uv CLI.

pytemplate-uv is an external tool: it is run as `uvx pytemplate-uv==3.0.* ... --json` through
the platform's subprocess runner, never imported, and never vendored. The integration relies
only on its documented contract: the render-request schema, JSON output (unknown fields are
ignored), and stable exit codes (0 success, 1 invalid request and nothing written, 2 a valid
request that failed while rendering or writing).
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quant_agent_os.runtime.runner import CommandResult, run_command
from quant_app_factory.domain.ports import (
    ScaffoldError,
    ScaffoldPlan,
    ScaffoldRenderError,
    ScaffoldRequest,
    ScaffoldRequestError,
    ScaffoldResult,
    ScaffoldUnavailableError,
)

PYTEMPLATE_UV = "pytemplate-uv==3.0.*"  # the one place the supported range is pinned
TIMEOUT_SECONDS = 300.0  # the first run downloads pytemplate-uv
JSON_CHARS = 2_000_000

Runner = Callable[..., CommandResult]


def render_request(request: ScaffoldRequest) -> str:
    """Return ``request`` as a pytemplate-uv TOML render request (schema request-v1)."""
    lines = [
        f"project_name = {json.dumps(request.project_name)}",
        f"profile = {json.dumps(request.profile)}",
        f"output_dir = {json.dumps(Path(request.output_dir).as_posix())}",
        f"components = {json.dumps(sorted(request.components))}",
        "no_input = true",
    ]
    if request.template is not None:
        lines.append(f"template = {json.dumps(request.template)}")
    lines.append("")
    lines.append("[data]")
    lines += [f"{key} = {json.dumps(value)}" for key, value in sorted(request.data.items())]
    return "\n".join(lines) + "\n"


class PytemplateUvScaffolder:
    """Plan and create projects with pytemplate-uv 3.0."""

    name = "pytemplate-uv"

    def __init__(
        self, *, runner: Runner = run_command, uvx: str = "uvx", timeout: float = TIMEOUT_SECONDS
    ) -> None:
        self._runner = runner
        self._uvx = uvx
        self._timeout = timeout

    @property
    def version(self) -> str:
        """The pinned pytemplate-uv requirement."""
        return PYTEMPLATE_UV

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan:
        """Return the exact files, dependencies, and checks; write nothing."""
        payload = self._run("plan", request)
        return ScaffoldPlan(
            project_path=payload["project_path"],
            target_exists=bool(payload.get("target_exists", False)),
            package_name=payload["package_name"],
            profile=payload["profile"],
            profile_version=str(payload["profile_version"]),
            components=_ids(payload.get("components", [])),
            dependencies=list(payload.get("dependencies", [])),
            dependency_groups=dict(payload.get("dependency_groups", {})),
            lifecycle_commands=list(payload.get("lifecycle_commands", [])),
            files=list(payload.get("files", [])),
            template=dict(payload.get("template") or {}),
            security_notes=list(payload.get("security_notes", [])),
        )

    def create(self, request: ScaffoldRequest) -> ScaffoldResult:
        """Create the project and return where it is and how to check it."""
        payload = self._run("create-from-config", request)
        return ScaffoldResult(
            project_path=payload["project_path"],
            manifest_path=payload.get("manifest_path"),
            profile=payload["profile"],
            profile_version=str(payload["profile_version"]),
            components=_ids(payload.get("components", [])),
            lifecycle_commands=list(payload.get("lifecycle_commands", [])),
        )

    def _run(self, command: str, request: ScaffoldRequest) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="quant-factory-") as workdir:
            request_file = Path(workdir) / "request.toml"
            request_file.write_text(render_request(request), "utf-8")
            result = self._runner(
                [self._uvx, PYTEMPLATE_UV, command, str(request_file), "--json"],
                cwd=Path(workdir),
                timeout=self._timeout,
                tail_chars=JSON_CHARS,
            )
        if result.timed_out:
            raise ScaffoldError(f"pytemplate-uv did not finish within {self._timeout:.0f} s.")
        if result.exit_code is None:
            raise ScaffoldUnavailableError(
                f"Cannot run {self._uvx}: {result.stderr_tail.strip()} "
                "Install uv (https://docs.astral.sh/uv/), which provides uvx."
            )
        try:
            payload = json.loads(result.stdout_tail)
        except json.JSONDecodeError as error:
            detail = (result.stderr_tail or result.stdout_tail).strip()[-500:]
            raise ScaffoldError(
                f"pytemplate-uv {command} exited {result.exit_code} without JSON output: {detail}"
            ) from error
        if not isinstance(payload, dict):
            raise ScaffoldError(f"pytemplate-uv {command} returned unexpected JSON.")
        if payload.get("ok") is True and result.exit_code == 0:
            return payload
        message = str((payload.get("error") or {}).get("message", "unknown error"))
        if result.exit_code == 1:
            raise ScaffoldRequestError(f"pytemplate-uv rejected the request: {message}")
        if result.exit_code == 2:
            raise ScaffoldRenderError(f"pytemplate-uv failed while rendering: {message}")
        raise ScaffoldError(f"pytemplate-uv {command} failed (exit {result.exit_code}): {message}")


def _ids(components: list[Any]) -> list[str]:
    return [item["id"] if isinstance(item, dict) else str(item) for item in components]
