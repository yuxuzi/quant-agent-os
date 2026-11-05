"""Tutorial lab: what the platform refuses, and how an operator says yes.

Usage: uv run python examples/tutorial/lab_guardrails.py

Each section tries something and prints what happened. Nothing here touches your files:
`send_order` only pretends, and the runs are kept in memory.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from quant_agent_os.providers import FakeModelProvider
from quant_agent_os.runtime.orchestrator import Orchestrator, PlanningError
from quant_agent_os.sdk import Platform
from quant_agent_os.tools import READ_ONLY, Permission, PermissionPolicy, ToolRegistry, tool


class Order(BaseModel):
    """A pretend order."""

    symbol: str
    quantity: int


class Receipt(BaseModel):
    """What the pretend broker returned."""

    message: str


@tool(
    name="send_order",
    description="Pretend to send an order to a broker. A demo: it sends nothing.",
    permissions=[Permission.EXTERNAL_ACTION],
    idempotent=False,
)
def send_order(params: Order) -> Receipt:
    """Return a receipt; a real tool would act on the outside world here."""
    return Receipt(message=f"Pretend order sent: {params.quantity} {params.symbol}")


ORDER = {"symbol": "ABC", "quantity": 100}
GRANTED = PermissionPolicy(granted=READ_ONLY | {Permission.EXTERNAL_ACTION})


def not_granted() -> None:
    """The default policy is read-only, so a consequential tool cannot run at all."""
    print("1. Default policy (read-only)")
    with Platform([send_order]) as platform:
        record = platform.call("send_order", ORDER)
        print(f"   status: {record.status}\n   error:  {record.error}")


def approval_gate() -> None:
    """Granting a permission makes a tool eligible; each call still needs its own yes."""
    print("\n2. Permission granted, no approval yet")
    with Platform([send_order], policy=GRANTED) as platform:
        waiting = platform.call("send_order", ORDER)
        run_id = waiting.run_id or ""
        stored = platform.get_run(run_id)
        assert stored is not None
        print(f"   status: {waiting.status}\n   run:    {stored.run.status}")

        print("\n3. The operator approves, and the same call resumes")
        platform.approve(run_id, approved_by="tutorial-operator")
        resumed = platform.resume(run_id)[0]
        stored = platform.get_run(run_id)
        assert stored is not None and resumed.output is not None
        print(f"   status: {resumed.status}\n   output: {resumed.output['message']}")
        print(f"   audit:  {[event.event for event in stored.events]}")

        print("\n4. The approval was for that one call, so the next call asks again")
        print(f"   status: {platform.call('send_order', ORDER).status}")


def bad_plans() -> None:
    """A model's plan is checked before anything runs, whatever the model says."""
    print("\n5. A plan naming a tool that is not registered")
    registry = ToolRegistry([send_order])
    plan = {"goal": "Delete it", "steps": [{"tool": "delete_everything", "arguments": {}}]}
    try:
        Orchestrator(registry, FakeModelProvider([json.dumps(plan)])).plan("Delete it")
    except PlanningError as error:
        print(f"   refused: {error}")

    print("\n6. A plan longer than the step limit")
    step = {"tool": "send_order", "arguments": ORDER}
    plan = {"goal": "Loop", "steps": [step, step, step]}
    try:
        Orchestrator(registry, FakeModelProvider([json.dumps(plan)]), max_steps=2).plan("Loop")
    except PlanningError as error:
        print(f"   refused: {error}")


if __name__ == "__main__":
    not_granted()
    approval_gate()
    bad_plans()
