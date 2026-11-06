"""``server_info``: the server's version, each tool's version and deprecation, and the digest.

Served as the resource ``qaos://server/info`` (decision 0015). A skill states the platform major
version it was written for, and tells the client to read this once and tell the user when the
majors differ.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from quant_agent_os.tools.catalog import ResourceSpec
from quant_agent_os.tools.contracts import ToolSpec, tool
from quant_agent_os.tools.registry import ToolRegistry

SERVER_INFO_URI = "qaos://server/info"
SERVER_INFO_TOOL = "server_info"


class ServerInfoRequest(BaseModel):
    """No arguments."""


class ToolVersion(BaseModel):
    """One served tool's version, and its replacement and removal date if deprecated."""

    name: str
    version: str
    replacement: str | None = None
    removal: str | None = Field(default=None, description="ISO date after which it may go.")


class ServerInfo(BaseModel):
    """What a client needs to check that its skills match this server."""

    platform_version: str
    platform_major: int
    tools: list[ToolVersion]
    registry_digest: str = Field(description="SHA-256 of the served tool contracts.")


def server_info_tool(platform_version: str, tools: list[ToolSpec[Any, Any]]) -> ToolSpec[Any, Any]:
    """Return ``server_info`` for a ``platform_version`` server of ``tools`` (and itself)."""

    @tool(
        name=SERVER_INFO_TOOL,
        description=(
            "The platform version and each tool's version and deprecation. Read once per "
            "session as qaos://server/info."
        ),
    )
    def server_info(params: ServerInfoRequest) -> ServerInfo:
        registry = ToolRegistry([*tools, server_info])
        return ServerInfo(
            platform_version=platform_version,
            platform_major=int(platform_version.split(".")[0]),
            tools=[
                ToolVersion(
                    name=spec.name,
                    version=spec.version,
                    replacement=spec.deprecated.replacement if spec.deprecated else None,
                    removal=spec.deprecated.removal.isoformat() if spec.deprecated else None,
                )
                for spec in registry.specs()
            ],
            registry_digest=registry.digest(),
        )

    return server_info


def server_info_resource() -> ResourceSpec:
    """Return the ``qaos://server/info`` resource, backed by ``server_info``."""
    return ResourceSpec(
        uri_template=SERVER_INFO_URI,
        name="server-info",
        description="The platform version and every tool's version (server_info).",
        tool="server_info",
    )
