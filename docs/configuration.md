# Configuration

Settings are read from environment variables prefixed `QUANT_AGENT_OS_` and from an optional
`.env` file in the working directory. `.env` is git-ignored; never commit it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `QUANT_AGENT_OS_MODEL_PROVIDER` | `fake` | Model provider adapter. Only `fake` ships today. |
| `QUANT_AGENT_OS_MODEL_NAME` | unset | Model identifier passed to the provider. |
| `QUANT_AGENT_OS_API_KEY` | unset | Provider credential; stored as a secret and never shown. |
| `QUANT_AGENT_OS_MAX_PLAN_STEPS` | `8` | Upper bound on plan length (1–50). |
| `QUANT_AGENT_OS_GRANTED_PERMISSIONS` | `["read_local_data"]` | JSON list of granted tool permissions. |
| `QUANT_AGENT_OS_LOG_LEVEL` | `INFO` | Logging level. |

`uv run quant-agent-os info` shows the active values. Secrets are reported only as `set` or
`unset`.

## Bring your own

- **Models:** implement `quant_agent_os.providers.ModelProvider`. Real vendor adapters will ship
  as optional dependencies; keep using `FakeModelProvider` in tests.
- **Data and documents:** pass them to tools as inputs or through your own data-access packages.
  QuantAgentOS never requires ownership of your databases, vector stores, or repositories.
- **Tools and MCP servers:** register your tools explicitly; expose them over MCP through
  `quant_agent_os.mcp`.

Supported integrations are listed here as they are added. Anything not listed is not claimed to
work.
