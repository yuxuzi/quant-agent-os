"""The universal tool contract: identity, typed I/O, permissions, limits, and description."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, get_type_hints

from pydantic import BaseModel

from quant_agent_os.tools.permissions import Permission

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ToolContractError(ValueError):
    """Raised when a tool definition violates the contract."""


@dataclass(frozen=True)
class ToolSpec(Generic[InputT, OutputT]):
    """A registered capability: a plain, independently testable function plus its contract.

    By convention, output models may expose ``warnings: list[str]`` and
    ``evidence: list[Evidence]``; reports collect both.
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

    def descriptor(self) -> dict[str, Any]:
        """Return a JSON-serializable description for planners, MCP, and documentation."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "permissions": sorted(permission.value for permission in self.permissions),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }


def tool(
    *,
    name: str,
    description: str,
    version: str = "1",
    permissions: Iterable[Permission] = (),
    timeout_seconds: float = 10.0,
    max_output_bytes: int = 100_000,
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
        )

    return decorate
