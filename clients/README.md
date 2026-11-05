# Client bundles

Configuration for clients other than Claude Code. Claude Code installs the plugin instead (see
[docs/getting-started.md](../docs/getting-started.md)). Each bundle has the MCP server command, the
same as `qaos connect`, plus the four skills' instructions as a rules file, because these clients
do not load skills.

| Client | Copy into your project | Then |
| --- | --- | --- |
| Cursor | `cursor/.cursor/` | Restart Cursor. |
| VS Code (Copilot agent mode) | `vscode/.vscode/` and `vscode/.github/` | Reload the window. |

The server is `qaos serve`, so [install `qaos`](../docs/getting-started.md) first. If the client
cannot find it, `qaos connect cursor` prints the entry with the command's full path. The server
reads the [one home](../docs/configuration.md): add data with `qaos ingest DIR` and `qaos data add
FILE`.

These files are generated from `skills/`. Do not edit them by
hand. After changing a skill, run `uv run python tests/server/client_bundles.py`;
a test fails while they are out of date.
