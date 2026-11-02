"""Resources and prompts: the MCP primitives beside tools, registered explicitly.

A `ResourceSpec` is a URI template backed by a registered tool: reading
``qaos://passage/{chunk_id}`` calls that tool with ``chunk_id`` as its argument, through the same
executor, so a resource read gets the tool's permissions, validation, limits, and run record.
There is no second path to the data. A `PromptSpec` is a named, parameterised workflow message a
client can offer its user; it runs nothing.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from urllib.parse import unquote

from quant_agent_os.tools.contracts import ToolContractError

_VARIABLE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_URI_TEMPLATE = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s{}]*(\{[a-z][a-z0-9_]*\}[^\s{}]*)*$")


class UnknownResourceError(LookupError):
    """Raised when no resource template matches a URI."""


class PromptArgumentError(ValueError):
    """Raised when a prompt is unknown or called with missing or unexpected arguments."""


@dataclass(frozen=True)
class ResourceSpec:
    """A URI template whose variables become the arguments of ``tool``."""

    uri_template: str
    name: str
    description: str
    tool: str
    mime_type: str = "application/json"

    def __post_init__(self) -> None:
        if not _URI_TEMPLATE.fullmatch(self.uri_template):
            raise ToolContractError(
                f"Resource '{self.name}' needs a URI template such as "
                f"'qaos://passage/{{chunk_id}}', not '{self.uri_template}'."
            )
        if not self.description.strip():
            raise ToolContractError(f"Resource '{self.name}' needs a description.")

    @property
    def variables(self) -> list[str]:
        """The template's variable names, in order."""
        return _VARIABLE.findall(self.uri_template)

    def match(self, uri: str) -> dict[str, str] | None:
        """Return the arguments ``uri`` gives this template, or None when it does not match.

        A variable matches one URI path segment and is percent-decoded.
        """
        parts = _VARIABLE.split(self.uri_template)
        pattern = "".join(
            re.escape(part) if index % 2 == 0 else f"(?P<{part}>[^/?#]+)"
            for index, part in enumerate(parts)
        )
        found = re.fullmatch(pattern, uri)
        if found is None:
            return None
        return {name: unquote(value) for name, value in found.groupdict().items()}


@dataclass(frozen=True)
class PromptArgument:
    """One argument a prompt takes."""

    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class PromptSpec:
    """A workflow message with ``{argument}`` placeholders, offered to a client's user."""

    name: str
    description: str
    template: str
    arguments: tuple[PromptArgument, ...] = field(default=())

    def __post_init__(self) -> None:
        placeholders = {
            name for _, name, _, _ in string.Formatter().parse(self.template) if name is not None
        }
        declared = {argument.name for argument in self.arguments}
        if placeholders != declared:
            raise ToolContractError(
                f"Prompt '{self.name}' declares arguments {sorted(declared)} but its template "
                f"uses {sorted(placeholders)}."
            )

    def render(self, arguments: dict[str, str] | None) -> str:
        """Return the message for ``arguments``; optional ones default to an empty string."""
        given = dict(arguments or {})
        declared = {argument.name for argument in self.arguments}
        missing = [a.name for a in self.arguments if a.required and not given.get(a.name)]
        unexpected = sorted(set(given) - declared)
        if missing or unexpected:
            raise PromptArgumentError(
                f"Prompt '{self.name}'"
                + (f" needs {', '.join(missing)}" if missing else "")
                + (" and" if missing and unexpected else "")
                + (f" does not take {', '.join(unexpected)}" if unexpected else "")
                + "."
            )
        return self.template.format(**{name: given.get(name, "") for name in declared})


def find_resource(resources: list[ResourceSpec], uri: str) -> tuple[ResourceSpec, dict[str, str]]:
    """Return the first resource whose template matches ``uri``, with its arguments."""
    for resource in resources:
        arguments = resource.match(uri)
        if arguments is not None:
            return resource, arguments
    raise UnknownResourceError(f"No resource matches '{uri}'.")
