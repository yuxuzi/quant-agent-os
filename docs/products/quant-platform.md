# Quant-Agent-OS platform server

One MCP server for every Quant-Agent-OS toolset, so a client (Claude, Cursor, Copilot) adds one
server instead of one per product. It is the composition root: the only package that imports
every product.

| Toolset | Tools | Resources | Prompts |
| --- | --- | --- | --- |
| `research` | `find_related_signals`, `get_source_passage`, `generate_strategy`, and `research_report` | `qaos://passage/{chunk_id}`, `qaos://strategy/{focus}` | `research_signal`, `report_on_signal` |
| `analytics` | `analyze_performance`, `analyze_risk` (series by `{dataset, column}`), `list_datasets` | `qaos://dataset/{dataset_id}` | `analyze_returns` |
| `factory` | `describe_template`, `check_spec`, `plan_app` | `qaos://template/{template}`, `qaos://template/{template}/{profile}` | `draft_app_spec` |

`research_report` runs a fixed plan with no model (`generate_strategy`, then
`analyze_performance` when a returns column is named) and returns the cited, evidence-linked
report in one call. Each resource is read through its tool, so it gets the same permissions,
validation, limits, and run record.

```bash
quant-agent-os dataset push returns.csv --id returns      # numbers stay out of the model
uv run quant-platform catalog --store .kb.db               # what a client will see
claude mcp add quant-agent-os -- uv run --directory "$PWD" quant-platform serve-mcp --store "$PWD/.kb.db"
```

`--toolsets research,analytics,factory` chooses what to serve (all by default). The research
store opens read-only, permissions come from `QUANT_AGENT_OS_GRANTED_PERMISSIONS` (read-only by
default), and every call is recorded in the run store unless `--no-record`.
