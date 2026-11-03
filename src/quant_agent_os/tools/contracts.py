"""The universal tool contract: identity, typed I/O, permissions, limits, and description."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, get_type_hints

from pydantic import BaseModel

from quant_agent_os.tools.permissions import Permission

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ToolContractError(ValueError):
    """Raised when a tool definition violates the contract."""


def parse_version(version: str) -> tuple[int, int, int]:
    """Return ``(major, minor, patch)`` of a ``MAJOR.MINOR.PATCH`` tool version (decision 0015)."""
    found = _SEMVER.fullmatch(version)
    if found is None:
        raise ToolContractError(f"Tool version '{version}' must be MAJOR.MINOR.PATCH, like 1.0.0.")
    major, minor, patch = (int(part) for part in found.groups())
    return major, minor, patch


@dataclass(frozen=True)
class Deprecation:
    """A deprecated tool's replacement and the date it may be removed (decision 0015, Q-16).

    A breaking change ships as a new tool name; the old one stays, deprecated, for at least 90
    days and one minor platform release.
    """

    replacement: str
    removal: dt.date

    def notice(self) -> str:
        """Return the sentence appended to the deprecated tool's description."""
        return f"Deprecated: use {self.replacement}; removed after {self.removal.isoformat()}."


@dataclass(frozen=True)
class ToolSpec(Generic[InputT, OutputT]):
    """A registered capability: a plain, independently testable function plus its contract.

    By convention, output models may expose ``warnings: list[str]``,
    ``evidence: list[Evidence]``, and ``data_versions: dict[str, str]`` (identifiers of the data
    the result depends on, such as a corpus snapshot); reports collect all three.

    ``deterministic`` means the same input and data versions give the same output;
    ``idempotent`` means a call can be repeated without additional side effects. ``version``
    is ``MAJOR.MINOR.PATCH``: major for a breaking schema or meaning change, minor for
    additions, patch for fixes (decision 0015).
    """

    name: str
    description: str
    version: str
    input_model: type[InputT]
    output_model: type[OutputT]
    function: Callable[[InputT], OutputT]
    permissions: frozenset[Permission] = frozenset()
    timeout_seconds: float = 10.0
    max_output_bytes: int = 100_000
    deterministic: bool = True
    idempotent: bool = True
    deprecated: Deprecation | None = None

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ToolContractError(
                f"Tool name '{self.name}' must be snake_case, start with a letter, "
                "and be at most 64 characters."
            )
        if not self.description.strip():
            raise ToolContractError(f"Tool '{self.name}' needs a description.")
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0:
            raise ToolContractError(f"Tool '{self.name}' limits must be positive.")
        parse_version(self.version)

    @property
    def title(self) -> str:
        """A human-readable display name derived from ``name``, such as "Analyze performance"."""
        return self.name.replace("_", " ").capitalize()

    @property
    def full_description(self) -> str:
        """The description clients see, with the deprecation notice when there is one."""
        if self.deprecated is None:
            return self.description
        return f"{self.description} {self.deprecated.notice()}"

    def descriptor(self) -> dict[str, Any]:
        """Return a JSON-serializable description for planners, MCP, and documentation.

        ``output_schema`` describes the serialized output (what MCP returns as
        ``structuredContent``), not the Python model's validation input.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(mode="serialization"),
            "permissions": sorted(permission.value for permission in self.permissions),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "deterministic": self.deterministic,
            "idempotent": self.idempotent,
            "deprecated": (
                None
                if self.deprecated is None
                else {
                    "replacement": self.deprecated.replacement,
                    "removal": self.deprecated.removal.isoformat(),
                }
            ),
        }


def tool(
    *,
    name: str,
    description: str,
    version: str = "1.0.0",
    permissions: Iterable[Permission] = (),
    timeout_seconds: float = 10.0,
    max_output_bytes: int = 100_000,
    deterministic: bool = True,
    idempotent: bool = True,
    deprecated: Deprecation | None = None,
) -> Callable[[Callable[[InputT], OutputT]], ToolSpec[InputT, OutputT]]:
    """Turn ``def fn(params: InputModel) -> OutputModel`` into a `ToolSpec`.

    The input and output models are read from the function's annotations, so the function
    stays an ordinary callable that tests and other packages can use directly through
    ``spec.function``.
    """

    def decorate(function: Callable[[InputT], OutputT]) -> ToolSpec[InputT, OutputT]:
        hints = get_type_hints(function)
        output_model = hints.pop("return", None)
        if len(hints) != 1:
            raise ToolContractError(f"Tool '{name}' must take exactly one typed parameter.")
        (input_model,) = hints.values()
        for model in (input_model, output_model):
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise ToolContractError(
                    f"Tool '{name}' parameter and return annotations must be pydantic models."
                )
        return ToolSpec(
            name=name,
            description=description,
            version=version,
            input_model=input_model,
            output_model=output_model,
            function=function,
            permissions=frozenset(permissions),
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            deterministic=deterministic,
            idempotent=idempotent,
            deprecated=deprecated,
        )

    return decorate
