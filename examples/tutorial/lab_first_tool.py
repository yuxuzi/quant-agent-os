"""Tutorial lab: write a tool, check it against the platform contract, and call it.

Usage: uv run python examples/tutorial/lab_first_tool.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quant_agent_os.evidence import Evidence, SourceRef
from quant_agent_os.sdk import Platform
from quant_agent_os.testing import check_tool
from quant_agent_os.tools import tool


class StreakInput(BaseModel):
    """The input schema: the platform validates every call against it."""

    series_id: str = Field(min_length=1, description="Name of the returns series")
    returns: list[float] = Field(min_length=1, description="Simple returns, e.g. 0.01 for 1%")


class StreakOutput(BaseModel):
    """The output schema: a value, plus evidence saying where it came from."""

    longest_losing_streak: int
    warnings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


@tool(
    name="longest_losing_streak",
    description="The largest number of consecutive periods with a negative return.",
)
def longest_losing_streak(params: StreakInput) -> StreakOutput:
    """Count the longest run of negative returns."""
    longest = current = 0
    for value in params.returns:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    source = SourceRef(source_id=params.series_id, location=f"{len(params.returns)} periods")
    statement = f"The longest losing streak of {params.series_id} is {longest} periods."
    return StreakOutput(
        longest_losing_streak=longest,
        warnings=["Fewer than 12 periods; read with care."] if len(params.returns) < 12 else [],
        evidence=[Evidence(kind="computed", statement=statement, sources=[source])],
    )


SAMPLE = {"series_id": "demo", "returns": [0.02, -0.01, -0.03, 0.01, -0.02, -0.01, -0.04, 0.03]}


def main() -> None:
    """Run the contract check, a good call, and a bad call, then read the run back."""
    check_tool(longest_losing_streak, SAMPLE)
    print("Contract check passed: the same result directly and through MCP, and deterministic.")

    with Platform([longest_losing_streak]) as platform:
        good = platform.call("longest_losing_streak", SAMPLE)
        print(f"\nGood call: {good.status}")
        if good.output is None:
            print(f"  error: {good.error}")
        else:
            print(f"  streak:   {good.output['longest_losing_streak']}")
            print(f"  warnings: {good.output['warnings']}")
            print(f"  evidence: {good.output['evidence'][0]['statement']}")

        bad = platform.call("longest_losing_streak", series_id="demo", returns="not a list")
        print(f"\nBad call: {bad.status}")
        print(f"  error: {(bad.error or '').splitlines()[0]}")

        stored = platform.get_run(good.run_id or "")
        assert stored is not None
        print(f"\nRecorded run: {stored.run.status}, by {stored.run.actor}")
        print(f"  input sha256: {(stored.calls[0].input_sha256 or '')[:16]}...")
        print(f"  audit trail:  {[event.event for event in stored.events]}")


if __name__ == "__main__":
    main()
