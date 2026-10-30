"""The single, bounded orchestrator: plan, validate, execute, and report."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from quant_agent_os.providers.base import ModelProvider
from quant_agent_os.reporting.generator import build_report
from quant_agent_os.reporting.models import Report
from quant_agent_os.runtime.executor import ToolExecutor
from quant_agent_os.runtime.plan import Plan
from quant_agent_os.tools.permissions import PermissionPolicy
from quant_agent_os.tools.registry import ToolRegistry


class PlanningError(ValueError):
    """Raised when the provider's plan is malformed or exceeds the allowed bounds."""


PLANNING_INSTRUCTIONS = """You plan quantitative research tasks using only the tools listed below.
Respond with JSON only, matching this schema:
{{"goal": str, "steps": [{{"tool": str, "arguments": object, "purpose": str}}]}}
Use at most {max_steps} steps. Use only listed tools and arguments that match their input
schemas. Do not invent data; if the request cannot be answered with these tools, return a
single step that explains why in its purpose.

Request: {goal}

Tools:
{tools}
"""


class Orchestrator:
    """Turn one request into a bounded plan, run it through the executor, and build a report.

    Retrieved documents and tool outputs are never fed back into planning, so injected text
    cannot change which tools run.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        provider: ModelProvider,
        policy: PermissionPolicy | None = None,
        *,
        max_steps: int = 8,
    ) -> None:
        self.registry = registry
        self.provider = provider
        self.policy = policy or PermissionPolicy()
        self.max_steps = max_steps
        self.executor = ToolExecutor(registry, self.policy)

    def plan(self, goal: str) -> Plan:
        """Ask the provider for a plan and reject anything outside the allowed bounds."""
        tools = [
            descriptor
            for descriptor in self.registry.descriptors()
            if self.policy.allows_tool(descriptor["name"])
        ]
        prompt = PLANNING_INSTRUCTIONS.format(
            max_steps=self.max_steps, goal=goal, tools=json.dumps(tools, indent=2)
        )
        try:
            plan = Plan.model_validate_json(self.provider.generate(prompt))
        except ValidationError as error:
            raise PlanningError(f"The provider returned an invalid plan: {error}") from error
        if len(plan.steps) > self.max_steps:
            raise PlanningError(
                f"The plan has {len(plan.steps)} steps; the limit is {self.max_steps}."
            )
        for step in plan.steps:
            if step.tool not in self.registry or not self.policy.allows_tool(step.tool):
                raise PlanningError(f"The plan uses a tool that is not available: '{step.tool}'.")
        return plan

    def run(self, goal: str) -> Report:
        """Plan and execute ``goal``; individual tool failures become report warnings."""
        started_at = datetime.now(UTC)
        run_id = uuid.uuid4().hex
        plan = self.plan(goal)
        records = [self.executor.execute(step, run_id=run_id) for step in plan.steps]
        return build_report(
            plan,
            records,
            run_id=run_id,
            provider=self.provider.name,
            registry_digest=self.registry.digest(),
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
