"""The scaffolder port: preview a project, then create it (decision 0005 gates the create)."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ScaffoldError(RuntimeError):
    """Raised when the scaffolder fails; the message is safe to show the operator."""


class ScaffoldUnavailableError(ScaffoldError):
    """The scaffolder program cannot be started (for example, `uvx` is not installed)."""


class ScaffoldRequestError(ScaffoldError):
    """The request was invalid; nothing was written (pytemplate-uv exit code 1)."""


class ScaffoldRenderError(ScaffoldError):
    """A valid request failed while rendering or writing (pytemplate-uv exit code 2)."""


class ScaffoldRequest(BaseModel):
    """A scaffolder-neutral render request, derived from an `AppSpec` by the planner."""

    project_name: str
    profile: str
    output_dir: str
    components: list[str] = Field(default_factory=list)
    data: dict[str, str] = Field(default_factory=dict)
    template: str | None = Field(default=None, description="Template pack directory, if any")


class ScaffoldPlan(BaseModel):
    """What `create` would do; nothing has been written."""

    project_path: str
    target_exists: bool
    package_name: str
    profile: str
    profile_version: str
    components: list[str]
    dependencies: list[str]
    dependency_groups: dict[str, list[str]] = Field(default_factory=dict)
    lifecycle_commands: list[str]
    files: list[str]
    template: dict[str, Any] = Field(default_factory=dict)
    security_notes: list[str] = Field(default_factory=list)


class ScaffoldResult(BaseModel):
    """A created project."""

    project_path: str
    manifest_path: str | None = None
    profile: str
    profile_version: str
    components: list[str]
    lifecycle_commands: list[str]


class ScaffoldProvider(Protocol):
    """Turns a `ScaffoldRequest` into a project. Swap in another scaffolder by implementing it."""

    name: str

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan:
        """Return the exact files and dependencies `create` would produce; write nothing."""
        ...

    def create(self, request: ScaffoldRequest) -> ScaffoldResult:
        """Create the project. Callers run this only inside an approved `create_app` call."""
        ...
