"""The constrained plan a provider proposes and the orchestrator validates before running."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """One call to a registered tool."""

    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""


class Plan(BaseModel):
    """An ordered, bounded list of tool calls for one request."""

    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1)
