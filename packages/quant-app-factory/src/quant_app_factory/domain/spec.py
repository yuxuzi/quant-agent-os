"""`AppSpec`: the single, reviewable contract for an application to generate."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

AppKind = Literal["agent-tool", "python-cli", "fastapi-service"]
PythonVersion = Literal["3.11", "3.12", "3.13", "3.14", "3.15"]
DEFAULT_QUANT_AGENT_OS_SOURCE = "git+https://github.com/yuxuzi/quant-agent-os"
_ONE_LINE = re.compile(r'^[^"\\\n]+$')  # what pytemplate-uv's questions accept


class SpecError(ValueError):
    """Raised when a spec file is missing, unreadable, or invalid."""


class AppSpec(BaseModel):
    """What to build. A model may draft it later (B-16); a person always reviews it.

    ``output_dir`` is the existing parent directory of the new project, as an absolute path;
    `load_spec` resolves a relative one against the spec file.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", description="Project slug")
    kind: AppKind
    description: str = Field(min_length=1, max_length=200)
    output_dir: str = Field(description="Existing parent directory (absolute)")
    author: str = "Your Name"
    email: str = "your@email.com"
    python_version: PythonVersion = "3.11"
    license: str = Field(
        default="MIT", pattern=r"^[A-Za-z0-9.+-]+( (AND|OR|WITH) [A-Za-z0-9.+-]+)*$"
    )
    components: list[str] = Field(default_factory=list, description="Optional components")
    tool_name: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*$", description="First tool (agent-tool only)"
    )
    quant_agent_os_source: str = Field(
        default=DEFAULT_QUANT_AGENT_OS_SOURCE,
        description="agent-tool only: a git+https URL or a local path to quant-agent-os",
    )

    @field_validator("quant_agent_os_source", mode="before")
    @classmethod
    def _forward_slashes(cls, value: object) -> object:
        """Write a local Windows path with forward slashes, as TOML templates need."""
        if isinstance(value, str) and not value.startswith("git+"):
            return value.replace("\\", "/")
        return value

    @field_validator("description", "author", "email", "quant_agent_os_source")
    @classmethod
    def _one_line(cls, value: str) -> str:
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
    def _kind_specific(self) -> AppSpec:
        if self.kind != "agent-tool" and self.tool_name is not None:
            raise ValueError("tool_name applies only to kind 'agent-tool'")
        return self

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
    try:
        return AppSpec.model_validate(data)
    except ValidationError as error:
        raise SpecError(f"{path} is not a valid app spec: {error}") from error
