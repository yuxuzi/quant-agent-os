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
  Quant-Agent-OS never requires ownership of your databases, vector stores, or repositories.
- **Tools and MCP servers:** register your tools explicitly; expose them over MCP through
  `quant_agent_os.mcp`.

### MCP server

`quant-research serve-mcp --store PATH` serves the research tools over stdio. It applies
`QUANT_AGENT_OS_GRANTED_PERMISSIONS`, which defaults to read-only. It warns on stderr when a
tool needs a permission that is not granted, for example `network` with a hosted embedder.
Install the transport with the `mcp` extra (`quant-agent-os[mcp]` or
`quant-agent-os-research[mcp]`); the development environment includes it.

Every call is recorded as a run in `runs.db` beside the store. Change the file with
`--runs PATH`, or turn recording off with `--no-record`. List recent runs with
`quant-research runs --store PATH`, and show one run's calls and audit trail with
`quant-research runs RUN_ID --store PATH`.

### Quant Research embeddings

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOYAGE_API_KEY` (or `QUANT_RESEARCH_VOYAGE_API_KEY`) | unset | Voyage AI key for `--embedder voyage`; a secret. |
| `OPENAI_API_KEY` (or `QUANT_RESEARCH_OPENAI_API_KEY`) | unset | OpenAI key for `--embedder openai`; a secret. |
| `QUANT_RESEARCH_EMBEDDING_TIMEOUT` | `30` | Seconds per embedding request (at most 300). |

Pick the model with `--embedding-model`, for example `voyage-4`, `voyage-4-large`, or
`text-embedding-3-large`. Ingest and search must use the same embedder and model.

## Supported integrations

| Integration | Status |
| --- | --- |
| MCP over stdio (Claude Code, Claude Desktop, other MCP clients) | Supported; tested with the SDK's client over a real subprocess |
| Voyage AI embeddings (HTTPS) | Supported; tested against a simulated API; live check is opt-in |
| OpenAI embeddings (HTTPS) | Supported; tested against a simulated API; live check is opt-in |

Supported integrations are listed here as they are added. Anything not listed is not claimed to
work.
