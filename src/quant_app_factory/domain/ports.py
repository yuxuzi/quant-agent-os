"""The scaffolder port: preview a project, then create it (decision 0005 gates the create)."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ScaffoldError(RuntimeError):
    """Raised when the scaffolder fails; the message is safe to show the operator."""


class ScaffoldRequestError(ScaffoldError):
    """The request was invalid; nothing was written."""


class ScaffoldRenderError(ScaffoldError):
    """A valid request failed while rendering or writing."""


class ScaffoldRequest(BaseModel):
    """A scaffolder-neutral render request, derived from an `AppSpec` by the planner."""

    project_name: str
    profile: str
    output_dir: str
    components: list[str] = Field(default_factory=list)
    data: dict[str, str] = Field(default_factory=dict, description="Answers passed as given")
    defaults: dict[str, str] = Field(
        default_factory=dict,
        description="Answers offered to the template; dropped where its profile does not ask",
    )
    template: str | None = Field(default=None, description="Template pack directory, if any")


class SettingInfo(BaseModel):
    """A runtime environment setting a component reads; never a stored value."""

    name: str
    type: str
    description: str
    secret: bool = False
    required_in_production: bool = False


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
    settings: list[SettingInfo] = Field(
        default_factory=list, description="Runtime settings the selected components read"
    )


class ScaffoldResult(BaseModel):
    """A created project."""

    project_path: str
    manifest_path: str | None = None
    profile: str
    profile_version: str
    components: list[str]
    lifecycle_commands: list[str]


class QuestionInfo(BaseModel):
    """One template question, as the template declares it."""

    name: str
    type: str = "str"
    help: str = ""
    choices: list[str] = Field(default_factory=list)
    default: str | None = Field(default=None, description="May be a template expression")
    when: str | None = Field(default=None, description="Condition, as written in the template")
    multiselect: bool = False
    secret: bool = False


class ComponentInfo(BaseModel):
    """An optional capability profiles can include."""

    id: str
    name: str
    description: str
    compatible_profiles: list[str]
    requires: list[str] = Field(default_factory=list)
    requires_any: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    settings: list[SettingInfo] = Field(default_factory=list)


class ProfileInfo(BaseModel):
    """A kind of project a template builds."""

    id: str
    name: str
    description: str
    default_components: list[str] = Field(default_factory=list)
    optional_components: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class TemplateDescription(BaseModel):
    """What a template asks and offers: profiles, components, questions, and settings."""

    profiles: list[ProfileInfo]
    components: list[ComponentInfo]
    questions: list[QuestionInfo]


class ScaffoldProvider(Protocol):
    """Turns a `ScaffoldRequest` into a project. Swap in another scaffolder by implementing it."""

    name: str

    def describe(self, template: str | None) -> TemplateDescription:
        """Describe a template (a pack directory, or None for the scaffolder's own)."""
        ...

    def plan(self, request: ScaffoldRequest) -> ScaffoldPlan:
        """Return the exact files and dependencies `create` would produce; write nothing."""
        ...

    def create(self, request: ScaffoldRequest) -> ScaffoldResult:
        """Create the project. Callers run this only inside an approved `create_app` call."""
        ...
