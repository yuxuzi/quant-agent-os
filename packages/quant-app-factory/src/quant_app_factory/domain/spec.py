"""`AppSpec`: the single, reviewable contract for an application to generate."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from quant_app_factory.domain import toml_text
from quant_app_factory.domain.secrets import ENV_NAME, secret_problem

AppKind = Literal[
    "agent-tool", "python-cli", "fastapi-service", "alpha-research", "backtest", "report"
]
PythonVersion = Literal["3.11", "3.12", "3.13", "3.14", "3.15"]
DEFAULT_QUANT_AGENT_OS_SOURCE = "git+https://github.com/yuxuzi/quant-agent-os"
_ONE_LINE = re.compile(r'^[^"\\\n]+$')  # what pytemplate-uv's questions accept

# Each kind is a shortcut for a (template, profile) pair.
KIND_TARGETS: dict[str, tuple[str, str]] = {
    "agent-tool": ("agent-tool", "agent-tool"),
    "python-cli": ("bundled", "python"),
    "fastapi-service": ("bundled", "fastapi"),
    "alpha-research": ("quant-baseline", "alpha-research"),
    "backtest": ("quant-baseline", "backtest"),
    "report": ("quant-baseline", "report"),
}
BUILT_IN_TEMPLATES = frozenset({"agent-tool", "quant-baseline", "bundled"})
# Answers set by spec fields or by pytemplate-uv itself; `answers` may not override them.
RESERVED_ANSWERS = frozenset(
    {
        "project_name",
        "package_name",
        "profile",
        "components",
        "description",
        "author",
        "email",
        "python_version",
        "license",
        "tool_name",
        "quant_agent_os_source",
    }
)
_ANSWER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class Identity(BaseModel):
    """Defaults for a generated project's author, email, and license."""

    model_config = ConfigDict(extra="forbid")

    author: str | None = None
    email: str | None = None
    license: str | None = None

    def merged(self, fallback: Identity) -> Identity:
        """Return this identity, with missing fields taken from ``fallback``."""
        return Identity(
            author=self.author or fallback.author,
            email=self.email or fallback.email,
            license=self.license or fallback.license,
        )


class SpecError(ValueError):
    """Raised when a spec file is missing, unreadable, or invalid."""


class AppSpec(BaseModel):
    """What to build. A model may draft it later (B-16); a person always reviews it.

    Choose what to build with ``kind`` (a shortcut), or with ``template`` and ``profile``:
    ``template`` is a built-in pack (``agent-tool``, ``quant-baseline``), ``bundled`` for
    pytemplate-uv's own profiles, or the path of your own pack. ``answers`` hold the
    template's other questions, such as ``frequency`` or ``cost_bps``.

    ``output_dir`` is the existing parent directory of the new project, as an absolute path;
    `load_spec` resolves a relative ``output_dir`` or ``template`` path against the spec file.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", description="Project slug")
    kind: AppKind | None = None
    template: str | None = Field(
        default=None, description="agent-tool, quant-baseline, bundled, or a pack directory"
    )
    profile: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    answers: dict[str, str] = Field(default_factory=dict, description="Other template answers")
    settings: dict[str, str] = Field(
        default_factory=dict,
        description="Runtime settings (environment variables the app reads): a value or env:NAME",
    )
    description: str = Field(min_length=1, max_length=200)
    output_dir: str = Field(description="Existing parent directory (absolute)")
    author: str | None = Field(default=None, description="Default: factory settings, git")
    email: str | None = Field(default=None, description="Default: factory settings, git")
    python_version: PythonVersion = "3.11"
    license: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9.+-]+( (AND|OR|WITH) [A-Za-z0-9.+-]+)*$"
    )
    components: list[str] = Field(default_factory=list, description="Optional components")
    tool_name: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*$", description="First tool (agent-tool only)"
    )
    quant_agent_os_source: str = Field(
        default=DEFAULT_QUANT_AGENT_OS_SOURCE,
        description="agent-tool only: a git+https URL or a local path to quant-agent-os",
    )

    @field_validator("quant_agent_os_source", "template", mode="before")
    @classmethod
    def _forward_slashes(cls, value: object) -> object:
        """Write a local Windows path with forward slashes, as TOML templates need."""
        if isinstance(value, str) and not value.startswith("git+"):
            return value.replace("\\", "/")
        return value

    @field_validator("template")
    @classmethod
    def _known_or_absolute(cls, value: str | None) -> str | None:
        if value is None or value in BUILT_IN_TEMPLATES:
            return value
        if not Path(value).is_absolute():
            raise ValueError(
                f"use {', '.join(sorted(BUILT_IN_TEMPLATES))}, or an absolute pack directory"
            )
        return value

    @field_validator("answers")
    @classmethod
    def _plain_answers(cls, answers: dict[str, str]) -> dict[str, str]:
        for name, value in answers.items():
            if not _ANSWER_NAME.match(name):
                raise ValueError(f"answer name {name!r} must be a lowercase identifier")
            if name in RESERVED_ANSWERS:
                raise ValueError(f"set {name!r} as a spec field, not in answers")
            if not _ONE_LINE.match(value) or not value.strip():
                raise ValueError(f"answer {name!r} must be one line, without quotes or backslashes")
            if problem := secret_problem(name, value):
                raise ValueError(problem)
        return answers

    @field_validator("settings")
    @classmethod
    def _settings_without_secrets(cls, settings: dict[str, str]) -> dict[str, str]:
        for name, value in settings.items():
            if not ENV_NAME.match(name):
                raise ValueError(f"setting {name!r} must be an environment-variable name")
            if not _ONE_LINE.match(value):
                raise ValueError(f"setting {name!r} must be one line, without quotes")
            if problem := secret_problem(name, value):
                raise ValueError(problem)
        return settings

    @field_validator("description", "author", "email", "quant_agent_os_source")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _ONE_LINE.match(value) or not value.strip():
            raise ValueError("use non-empty text on one line without double quotes or backslashes")
        return value

    @field_validator("output_dir")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("must be an absolute path")
        return value

    @model_validator(mode="after")
    def _target_specific(self) -> AppSpec:
        if self.kind is None and (self.template is None or self.profile is None):
            raise ValueError("set kind, or both template and profile")
        if self.kind is not None:
            expected = KIND_TARGETS[self.kind]
            given = (self.template or expected[0], self.profile or expected[1])
            if given != expected:
                raise ValueError(
                    f"kind {self.kind!r} means template {expected[0]!r} with profile "
                    f"{expected[1]!r}; set kind or template and profile, not a mix"
                )
        if self.target != ("agent-tool", "agent-tool") and self.tool_name is not None:
            raise ValueError("tool_name applies only to the agent-tool profile")
        return self

    @property
    def target(self) -> tuple[str, str]:
        """The ``(template, profile)`` this spec builds."""
        if self.kind is not None:
            return KIND_TARGETS[self.kind]
        assert self.template is not None and self.profile is not None  # by validation
        return self.template, self.profile

    @property
    def package_name(self) -> str:
        """The importable package name pytemplate-uv derives from the project name."""
        return self.name.replace("-", "_")


def load_spec(path: Path) -> AppSpec:
    """Read an `AppSpec` from a TOML file; a relative ``output_dir`` is relative to the file."""
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SpecError(f"Cannot read the app spec {path}: {error}") from error
    output_dir = Path(str(data.get("output_dir", ".")))
    if not output_dir.is_absolute():
        output_dir = path.resolve().parent / output_dir
    data["output_dir"] = str(output_dir.resolve())
    template = data.get("template")
    if isinstance(template, str) and template not in BUILT_IN_TEMPLATES:
        pack = Path(template).expanduser()
        if not pack.is_absolute():
            pack = path.resolve().parent / pack
        data["template"] = pack.resolve().as_posix()
    try:
        return AppSpec.model_validate(data)
    except ValidationError as error:
        raise SpecError(f"{path} is not a valid app spec: {error}") from error


def dump_spec(spec: AppSpec) -> str:
    """Return ``spec`` as a TOML spec file; fields left at their defaults are omitted."""
    data = spec.model_dump(exclude_defaults=True)
    answers = data.pop("answers", None) or {}
    settings = data.pop("settings", None) or {}
    order = [name for name in AppSpec.model_fields if name in data]
    lines = toml_text.table(None, {name: data[name] for name in order})
    lines += toml_text.table("answers", answers)
    lines += toml_text.table("settings", settings)
    return "\n".join(lines).rstrip() + "\n"
