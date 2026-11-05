"""Tutorial helper: print a REST response from the platform as a few readable lines.

Usage: curl ... | uv run python examples/tutorial/show.py
"""

from __future__ import annotations

import json
import sys
from typing import Any


def describe(data: Any) -> list[str]:
    """Return readable lines for a tool list, a run list, a tool call, or a refusal."""
    if isinstance(data, list) and data and "name" in data[0]:
        return ["tools: " + ", ".join(item["name"] for item in data)]
    if isinstance(data, list):
        return [
            f"run by {run['principal']} in tenant {run['tenant']}: {run['goal']} {run['status']}"
            for run in data
        ] or ["no runs"]
    if "detail" in data:
        return [f"refused: {data['detail']}"]
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
    if output.get("spec"):
        lines.append(f"open questions: {len(output['spec']['open_questions'])}")
    return lines


if __name__ == "__main__":
    print("\n".join(describe(json.load(sys.stdin))))
