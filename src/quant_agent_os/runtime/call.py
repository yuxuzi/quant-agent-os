"""One call to a registered tool, as every interface hands it to the executor."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """One call to a registered tool."""

    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""


class Plan(BaseModel):
    """An ordered, bounded list of tool calls for one request."""

    goal: str = Field(min_length=1)
    steps: list[ToolCall] = Field(min_length=1)
