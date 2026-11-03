# Client bundles

Configuration for clients other than Claude Code. Claude Code installs the plugin instead (see
[docs/getting-started.md](../docs/getting-started.md)). Each bundle has the same MCP server
command as the plugin, plus the four skills' instructions as a rules file, because these clients
do not load skills.

| Client | Copy into your project | Then |
| --- | --- | --- |
| Cursor | `cursor/.cursor/` | Edit `.cursor/mcp.json`: set the repository path and `QUANT_AGENT_OS_STORE`. |
| VS Code (Copilot agent mode) | `vscode/.vscode/` and `vscode/.github/` | Edit `.vscode/mcp.json` the same way. |

- **The paths.** `/absolute/path/to/quant-agent-os` is your clone of this repository, which the
  server runs from with `uv`. `/absolute/path/to/knowledge.db` is the store you built with
  `quant-research ingest`; remove the variable to serve without the research toolset.
- **Datasets** for analytics are read from `~/.quant-agent-os/datasets`, or from
  `QUANT_AGENT_OS_DATASETS_DIR`. Add one with `quant-agent-os dataset push FILE --id ID`.

These files are generated from `skills/` and `.claude-plugin/plugin.json`. Do not edit them by
hand. After changing a skill, run `uv run python apps/quant-platform/tests/client_bundles.py`;
a test fails while they are out of date.
