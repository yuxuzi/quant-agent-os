"""Tool contract, registry, and permissions."""

from quant_agent_os.tools.catalog import (
    PromptArgument,
    PromptArgumentError,
    PromptSpec,
    ResourceSpec,
    UnknownResourceError,
)
from quant_agent_os.tools.contracts import (
    Deprecation,
    ToolContractError,
    ToolSpec,
    parse_version,
    tool,
)
from quant_agent_os.tools.permissions import (
    CONSEQUENTIAL,
    READ_ONLY,
    Permission,
    PermissionDeniedError,
    PermissionPolicy,
)
from quant_agent_os.tools.registry import DuplicateToolError, ToolRegistry, UnknownToolError

__all__ = [
    "CONSEQUENTIAL",
    "READ_ONLY",
    "Deprecation",
    "DuplicateToolError",
    "Permission",
    "PermissionDeniedError",
    "PermissionPolicy",
    "PromptArgument",
    "PromptArgumentError",
    "PromptSpec",
    "ResourceSpec",
    "ToolContractError",
    "ToolRegistry",
    "ToolSpec",
    "UnknownResourceError",
    "UnknownToolError",
    "parse_version",
    "tool",
]
