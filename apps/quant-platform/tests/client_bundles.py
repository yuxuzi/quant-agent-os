"""Client bundles for Cursor and VS Code (Copilot), generated from the skills and the plugin.

Claude Code installs the plugin (`.claude-plugin/plugin.json`), which ships `skills/` and the MCP
server command. Clients without skills get the same server command and the skills' text as a
rules file instead. Run this after changing a skill or the plugin's server command:

    uv run python apps/quant-platform/tests/client_bundles.py

`test_client_bundles.py` fails while the committed files are out of date.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "skills"
CLIENTS = ROOT / "clients"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
REPOSITORY = "/absolute/path/to/quant-agent-os"
STORE = "/absolute/path/to/knowledge.db"
SERVER = "quant-agent-os"
DESCRIPTION = (
    "Quant-Agent-OS: search the user's research corpus with citations, compute performance and "
    "risk metrics by dataset reference, draft cited strategy specs, and check App Factory specs."
)
GENERATED = "Generated from skills/ by apps/quant-platform/tests/client_bundles.py; do not edit."


def server_args() -> list[str]:
    """Return the plugin's server arguments with the repository path as a placeholder."""
    manifest = json.loads(PLUGIN.read_text("utf-8"))
    args: list[str] = manifest["mcpServers"][SERVER]["args"]
    return [REPOSITORY if arg == "${CLAUDE_PLUGIN_ROOT}" else arg for arg in args]


def rules() -> str:
    """Return every skill's description and instructions as one Markdown document."""
    parts = [f"# Quant-Agent-OS\n\n<!-- {GENERATED} -->"]
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", path.read_text("utf-8"), re.DOTALL)
        assert match, f"{path} has no frontmatter"
        description = re.search(r"^description: (.+)$", match.group(1), re.MULTILINE)
        assert description, f"{path} has no description"
        body = [line for line in match.group(2).strip().splitlines() if not line.startswith("# ")]
        demoted = ["#" + line if line.startswith("#") else line for line in body]
        parts.append(
            f"## {path.parent.name}\n\n*{description.group(1)}*\n\n" + "\n".join(demoted).strip()
        )
    return "\n\n".join(parts) + "\n"


def bundles() -> dict[Path, str]:
    """Return each generated client file and its content."""
    server = {"command": "uv", "args": server_args(), "env": {"QUANT_AGENT_OS_STORE": STORE}}
    text = rules()
    return {
        CLIENTS / "cursor" / ".cursor" / "mcp.json": _json({"mcpServers": {SERVER: server}}),
        CLIENTS / "cursor" / ".cursor" / "rules" / "quant-agent-os.mdc": (
            f"---\ndescription: {DESCRIPTION}\nalwaysApply: false\n---\n\n{text}"
        ),
        CLIENTS / "vscode" / ".vscode" / "mcp.json": _json(
            {"servers": {SERVER: {"type": "stdio", **server}}}
        ),
        CLIENTS / "vscode" / ".github" / "instructions" / "quant-agent-os.instructions.md": (
            f'---\napplyTo: "**"\ndescription: {DESCRIPTION}\n---\n\n{text}'
        ),
    }


def _json(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


def main() -> None:
    """Write every client bundle file."""
    for path, content in bundles().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, "utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
