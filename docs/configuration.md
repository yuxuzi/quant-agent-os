# Configuration

Everything the platform keeps is in one home: `QUANT_AGENT_OS_HOME`, by default
`~/.quant-agent-os`. Every command uses it with no flags.

| File | Holds |
| --- | --- |
| `config.toml` | Optional settings: `log_level`, `store`, `datasets_dir`, `runs_file`, `api_keys_file`, and `access_file`. |
| `.env` | Optional environment variables, such as provider keys. Never commit it. |
| `knowledge.db` | The research knowledge store. |
| `datasets/` | Datasets that tools read by id. |
| `runs.db` | The audit: every recorded run, from the server and from `qaos app`. |
| `api-keys.toml` | Hashes of API keys for `qaos serve --http`. |
| `factory.toml`, `packs/` | App Factory settings and fetched template packs. |

A setting comes from a `QUANT_AGENT_OS_*` environment variable, then the home's `.env`, then
`config.toml`. A `.env` in the working directory is never read, because a client that launches the
server chooses that directory. `config.toml` refuses anything else, including permissions, so
permissions come only from the environment or the home's `.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `QUANT_AGENT_OS_GRANTED_PERMISSIONS` | `["read_local_data"]` | JSON list of granted tool permissions. |
| `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS` | `[]` | JSON list of consequential permissions approved for every call (a standing approval). Leave it empty unless one permission must run unattended, such as `network` for a hosted embedder. |
| `QUANT_AGENT_OS_LOG_LEVEL` | `INFO` | Logging level. |
| `QUANT_AGENT_OS_STORE` | `knowledge.db` in the home | SQLite path or `postgresql://` URL. |
| `QUANT_AGENT_OS_DATASETS_DIR` | `datasets/` in the home | Datasets directory. |
| `QUANT_AGENT_OS_RUNS_FILE` | `runs.db` in the home | Run store. |
| `QUANT_AGENT_OS_API_KEYS_FILE` | `api-keys.toml` in the home | API keys file. |
| `QUANT_AGENT_OS_ACCESS_FILE` | none | Over HTTP: which groups reach which tools and datasets. See [deployment](deployment.md#9-decide-who-reaches-what). |
| `QUANT_AGENT_OS_SCHEMA` | `quant_research` | PostgreSQL schema that `qaos ingest` and `qaos search` use. |

`qaos doctor` checks the setup and shows what is off, with no secret values.

## Bring your own

- **Models:** the platform calls no model. Your client plans, or your own code does, with its
  vendor's tool loop over `tool_definitions` and `Platform.call`
  ([example](../examples/your-own-model/drive_tools.py)).
- **Data and documents:** pass them to tools as inputs or through your own data-access packages.
  Quant-Agent-OS never requires ownership of your databases, vector stores, or repositories.
- **Tools and MCP servers:** register your tools explicitly; expose them over MCP through
  `quant_agent_os.mcp`.

### MCP server

`qaos serve` serves the default toolsets over stdio, and `--toolsets` chooses others. It applies
`QUANT_AGENT_OS_GRANTED_PERMISSIONS` (read-only by default) and
`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`. It warns on stderr when a tool needs a permission that
is not granted, or one that needs approval for every call. For example, a hosted embedder needs
`network` both granted and pre-approved, because MCP clients cannot approve calls.
It needs the `server` extra (`quant-agent-os[server]`); the development environment includes it.

Every call is recorded as a run in the home's `runs.db`. Change the file with `--runs PATH`, or
turn recording off with `--no-record`. List recent runs with `qaos runs`, and show one run's calls
and audit trail with `qaos runs RUN_ID`.

### Quant Research embeddings

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOYAGE_API_KEY` | unset | Voyage AI key for `--embedder voyage`; a secret. |
| `OPENAI_API_KEY` | unset | OpenAI key for `--embedder openai`; a secret. |
| `QUANT_AGENT_OS_EMBEDDING_TIMEOUT` | `30` | Seconds per embedding request (at most 300). |

Pick the model with `--embedding-model`, for example `voyage-4`, `voyage-4-large`, or
`text-embedding-3-large`. Ingest, search, and `qaos serve` must use the same embedder and model.

## Supported integrations

| Integration | Status |
| --- | --- |
| MCP over stdio (Claude Code, Claude Desktop, other MCP clients) | Supported; tested with the SDK's client over a real subprocess |
| Voyage AI embeddings (HTTPS) | Supported; tested against a simulated API; live check is opt-in |
| OpenAI embeddings (HTTPS) | Supported; tested against a simulated API; live check is opt-in |

Supported integrations are listed here as they are added. Anything not listed is not claimed to
work.

### Quant App Factory

`qaos app plan SPEC.toml` previews a project: the profile, the reasons, the dependencies,
and the exact files. pytemplate-uv renders the preview into a temporary directory, never the
target.

`qaos app create SPEC.toml` shows the same preview and asks you to confirm. Your
confirmation is the approval for that exact spec. It then creates the project and runs the
project's checks (`uv sync`, ruff, mypy, pytest).

Runs and approvals are recorded in the home's `runs.db` (`--runs PATH`). It
needs `uv`, and network access only for the generated project's `uv sync`.

An app spec is TOML:

```toml
name = "signal-tools"            # project slug
template = "agent-tool"          # agent-tool, quant-baseline, bundled, a registered name, or a path
profile = "agent-tool"           # bundled offers python and fastapi; quant-baseline quant-lab
description = "Signal tools for Quant-Agent-OS"
output_dir = "projects"          # existing parent directory, relative to this file
tool_name = "count_signals"      # agent-tool only
# quant_agent_os_source = "../quant-agent-os"   # agent-tool only; default: the git repository
# components = ["database"]      # python-cli: container; fastapi-service: database, worker,
#                                #   auth with auth-api-key or auth-oidc, container
```

A quant spec, or one for your own pack:

```toml
name = "momo-lab"
template = "quant-baseline"
profile = "quant-lab"
description = "Momentum research"
output_dir = "."
components = ["data-parquet"]    # optional: data-parquet, agent-tools

[answers]                        # the template's other questions
frequency = "daily"              # daily, weekly, or monthly
cost_bps = "3"                   # also max_horizon (IC decay)

# template = "../packs/my-pack"  # your own pytemplate-uv pack, relative to this file
# profile = "my-profile"
```

A spec always names `template` and `profile`. On the command line, `qaos app new` also takes the
shortcuts `agent-tool`, `quant-lab`, `python`, and `fastapi`, and writes both fields for you.

The research parameters themselves (signal, lookback, trade lag, quantiles, costs, rebalance
schedule, and delisting return) live in the generated project's `config.toml`, not in the spec:
they change from one run to the next, and every results file records the values it used.

## Factory settings

[Decision 0008](decisions/0008-factory-settings.md), backlog B-24. App Factory reuses these
settings in specs and in `qaos app new`. Secrets are stored only as environment-variable
names:

```toml
# factory.toml in the home
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

[templates.firm-backtest]  # added by `qaos app template add`
source = "gh:firm/quant-packs"
ref = "v1.2.0"
commit = "<the commit v1.2.0 named when it was fetched>"
digest = "<SHA-256 of the fetched files>"
path = "<home>/packs/firm-backtest/v1.2.0"
```

Edit the file directly, or use `qaos app settings show|set|remove`, for example
`qaos app settings set connections.research-pg.password_env RESEARCH_PG_PASSWORD`.
Values that look like secrets are refused,
and so are unknown fields.

`[identity]` fills a spec's missing author, email, and license; missing ones fall back to
`git config`, then the template's own defaults.

### Templates

- **`qaos app template list`** shows every template as `pack/profile`, the name `qaos app new` takes, with
  each pack's version and where it came from.
- **`qaos app template fork quant-baseline/quant-lab DIR [--profile-id ID]`** copies a
  built-in profile into your own pack, checks it, and registers it under the directory's name
  (`--name` to choose).
- **`qaos app template check NAME_OR_DIR`** loads a pack and plans every profile with
  default answers. For a git pack, it first fails if the cached files no longer match the recorded digest.
- **`qaos app template add DIR --name NAME`** registers a local pack.
- **`qaos app template add gh:org/repo --name NAME --ref TAG`** fetches a git pack once,
  at that tag, into `packs/` beside `factory.toml`, so plans stay offline. It records the commit and a digest
  of the files. Add `--commit SHA --digest HASH`, as the pack's author or an index publishes them, to refuse a
  pack that differs: a tag can be moved, and these cannot. They are the values pytemplate-uv's
  `--template-commit` and `--template-digest` take.

A spec then says `template = "NAME"` with `profile = "..."`.

### Databases

The `data-database` component of `quant-research` loads prices with one
read-only query from PostgreSQL, SQLite, or DuckDB.
- **Naming a connection.** `connection = "research-pg"` in a spec adds the component and fills
  its answers from `factory.toml`, and the spec's own answers win.
  `qaos app new` offers saved connections, and offers to save a new one.
- **What the generated app reads.** It reads its credentials only from `DATABASE_URL` (a full
  URL, which wins) or `DATABASE_PASSWORD`, listed in its `.env.example`. Its
  `check-connection` command connects, then exits.
- **The factory's check.** `qaos app create --check-connection` runs that check after the
  project's own checks.
  - It passes only the variables the spec's settings reference, such as
    `DATABASE_PASSWORD = "env:RESEARCH_PG_PASSWORD"`.
  - It redacts their values from the output, including URL-encoded forms.
  - It runs only for projects from built-in templates, so your secret never reaches pack code
    you have not reviewed.
- **Retrying.** `qaos app check SPEC [--check-connection]` re-runs the checks of a
  project that already exists, with its own approval.
- **TLS.** PostgreSQL connections verify the server certificate (`db_sslmode`, default
  `verify-full`). Point `PGSSLROOTCERT` at your CA certificate, or add `sslrootcert=system` to
  `DATABASE_URL` for a publicly signed server. `disable` is for local development only.

### Guided specs

- **In the terminal:** `qaos app new [--template T] [--profile P] [-o spec.toml]` asks
  the template's own questions. It skips those that do not apply, and asks for secret
  settings as environment-variable names. It writes the spec, checks it, and offers to create
  the project.
- **From Claude:** `qaos serve --toolsets factory` serves `describe_template` and `check_spec` over
  stdio. Claude can then guide you to a ready spec and give you its TOML.
  You still create from the terminal, because MCP clients cannot approve `create_app` yet.

A server started by Claude Desktop does not see your shell's environment, so run
`qaos app create` from a terminal when a connection check needs those variables.
