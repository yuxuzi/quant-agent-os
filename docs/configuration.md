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
| `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS` | `[]` | JSON list of consequential permissions approved for every call (a standing approval). Leave it empty unless one permission must run unattended, such as `network` for a hosted embedder. |
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
`QUANT_AGENT_OS_GRANTED_PERMISSIONS` (read-only by default) and
`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`. It warns on stderr when a tool needs a permission that
is not granted, or one that needs approval for every call. For example, a hosted embedder needs
`network` both granted and pre-approved, because MCP clients cannot approve calls.
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

### Quant App Factory

`quant-factory plan SPEC.toml` previews a project: the profile, the reasons, the dependencies,
and the exact files. pytemplate-uv renders the preview into a temporary directory, never the
target.

`quant-factory create SPEC.toml` shows the same preview and asks you to confirm. Your
confirmation is the approval for that exact spec. It then creates the project and runs the
project's checks (`uv sync`, ruff, mypy, pytest).

Runs and approvals are recorded in `~/.quant-agent-os/factory-runs.db` (`--runs PATH`). It
needs `uv`, and network access only for the generated project's `uv sync`.

An app spec is TOML:

```toml
name = "signal-tools"            # project slug
kind = "agent-tool"              # agent-tool, python-cli, fastapi-service, alpha-research,
                                 #   backtest, or report
description = "Signal tools for Quant-Agent-OS"
output_dir = "projects"          # existing parent directory, relative to this file
tool_name = "count_signals"      # agent-tool only
# quant_agent_os_source = "../quant-agent-os"   # agent-tool only; default: the git repository
# components = ["database"]      # python-cli: container; fastapi-service: database, worker,
#                                #   auth with auth-api-key or auth-oidc, container
```

A quant spec, or one for your own pack:

```toml
name = "momo-backtest"
kind = "backtest"                # or: template = "quant-baseline" and profile = "backtest"
description = "Momentum long-short backtest"
output_dir = "."
components = ["data-parquet"]    # optional: data-parquet, agent-tools

[answers]                        # the template's other questions
frequency = "daily"              # daily, weekly, or monthly
cost_bps = "3"                   # backtest only; alpha-research asks max_horizon

# template = "../packs/my-pack"  # your own pytemplate-uv pack, relative to this file
# profile = "my-profile"
```

## Factory settings

[Decision 0008](decisions/0008-factory-settings.md), backlog B-24. App Factory reuses these
settings in specs and in `quant-factory new`. Secrets are stored only as environment-variable
names:

```toml
# ~/.quant-agent-os/factory.toml
[identity]                 # falls back to git config
author = "Your Name"
email = "you@example.com"

[connections.research-pg]
kind = "postgres"
host = "db.internal"
port = 5432
database = "research"
user = "analyst"
schema = "prices"
read_only = true
password_env = "RESEARCH_PG_PASSWORD"   # the variable's name, never the password

[templates.firm-backtest]  # added by `quant-factory template add`
source = "gh:firm/quant-packs"
ref = "v1.2.0"
```

Edit the file directly, or use `quant-factory settings show|set|remove`, for example
`quant-factory settings set connections.research-pg.password_env RESEARCH_PG_PASSWORD`.
`QUANT_FACTORY_SETTINGS` points to another file. Values that look like secrets are refused,
and so are unknown fields.

`[identity]` fills a spec's missing author, email, and license; missing ones fall back to
`git config`, then the template's own defaults.

### Databases

The `data-database` component of `alpha-research` and `backtest` loads prices with one
read-only query from PostgreSQL, SQLite, or DuckDB.
- **Naming a connection.** `connection = "research-pg"` in a spec adds the component and fills
  its answers from `factory.toml`, and the spec's own answers win.
  `quant-factory new` offers saved connections, and offers to save a new one.
- **What the generated app reads.** It reads its credentials only from `DATABASE_URL` (a full
  URL, which wins) or `DATABASE_PASSWORD`, listed in its `.env.example`. Its
  `--check-connection` flag connects, then exits.
- **The factory's check.** `quant-factory create --check-connection` runs that check after the
  project's own checks. It passes only the variables the spec's settings reference, such as
  `DATABASE_PASSWORD = "env:RESEARCH_PG_PASSWORD"`, and redacts their values from the
  output.

### Guided specs

- **In the terminal:** `quant-factory new [--template T] [--profile P] [-o spec.toml]` asks
  the template's own questions. It skips those that do not apply, and asks for secret
  settings as environment-variable names. It writes the spec, checks it, and offers to create
  the project.
- **From Claude:** `quant-factory serve-mcp` serves `describe_template`, `check_spec`, and
  `plan_app` over stdio. Claude can then guide you to a ready spec and give you its TOML.
  You still create from the terminal, because MCP clients cannot approve `create_app` yet.

A server started by Claude Desktop does not see your shell's environment, so run
`quant-factory create` from a terminal when a connection check needs those variables.
