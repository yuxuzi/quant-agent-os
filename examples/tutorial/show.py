"""Tutorial helper: print a REST response from the platform as a few readable lines.

Usage: curl ... | uv run python examples/tutorial/show.py
"""

from __future__ import annotations

import json
import sys
from typing import Any


def describe(data: Any) -> list[str]:
    """Return readable lines for a tool list, a run list, one run, a tool call, or a refusal."""
    if isinstance(data, list) and data and "name" in data[0]:
        return ["tools: " + ", ".join(item["name"] for item in data)]
    if isinstance(data, list):
        return [
            f"run by {run['principal']} in tenant {run['tenant']}: {run['goal']} {run['status']}"
            for run in data
        ] or ["nothing listed"]
    if "detail" in data:
        return [f"refused: {data['detail']}"]
    if "calls" in data:
        run = data["run"]
        return [
            f"{run['principal']} called {call['tool']}: {call['status']}, "
            f"refused: {', '.join(call['refused']) or 'nothing'}"
            for call in data["calls"]
        ]
    lines = [f"status: {data['status']}"]
    if data["status"] != "ok":
        lines.append(f"error: {data['error']}")
        lines.append(f"hint: {data['hint']}")
        return lines
    output = data["output"]
    if output.get("related"):
        lines.append("related: " + ", ".join(item["name"] for item in output["related"]))
    if output.get("passages"):
        sources = sorted({item["source"]["source_id"] for item in output["passages"]})
        lines.append("cited: " + ", ".join(sources))
    if output.get("metrics"):
        lines.append(f"sharpe_ratio: {output['metrics']['sharpe_ratio']:.4f}")
    if isinstance(output.get("result"), float):
        lines.append(f"result: {output['result']:.4f}, convention: {output['convention']}")
    if "breaches" in output:
        failed = [check["name"] for check in output["checks"] if check["breached"]]
        lines.append(
            f"breaches: {output['breaches']} of {len(output['checks'])}: {', '.join(failed)}"
        )
    if "datasets" in output:
        lines.append("datasets: " + ", ".join(item["id"] for item in output["datasets"]))
    if output.get("spec"):
        lines.append(f"open questions: {len(output['spec']['open_questions'])}")
    return lines


if __name__ == "__main__":
    text = sys.stdin.read()
    if not text.strip():
        sys.exit(
            "no answer: is the server running in Terminal 2, and did it answer within 60 seconds? "
            "Lab 5 starts it."
        )
    print("\n".join(describe(json.loads(text))))
