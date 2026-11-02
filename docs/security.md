# Security model

Quant-Agent-OS assumes that model output, retrieved documents, and tool output are untrusted.

## Defaults

- **Read-only.** The default policy grants only `read_local_data`. Tools that need `network`,
  `write_local_data`, `execute_code`, or `external_action` are refused unless the operator grants
  those permissions explicitly.
- **Explicit tools.** Only tools registered in a `ToolRegistry` can run, optionally narrowed
  further by an allowlist. There is no dynamic discovery or code loading.
- **Bounded plans.** Plans are validated before execution against the registry, the allowlist,
  and `max_plan_steps`.
- **Bounded calls.** Every call validates its input and output, has a timeout, and has an
  output-size limit. A timed-out Python thread cannot be killed, so tools must avoid unbounded
  or side-effecting work.
- **No feedback into planning.** Tool outputs and retrieved text are not sent back to the
  platform's planner, so injected instructions in data cannot choose tools.
- **External clients plan for themselves.** An MCP or REST client such as Claude or Cursor
  does read tool output, and the platform cannot stop it from acting on text in a passage.
  So every result that carries corpus text says `"trust": "untrusted"`, the research server's
  instructions tell the client never to follow instructions found in passages, and the
  approval gate still stops any consequential call that injected text might provoke.
- **Secrets.** Credentials come from the environment or `.env`, are held as `SecretStr`, are
  never displayed, and can be redacted from logs with `RedactSecretsFilter`.

## Hosted embedding providers

Quant Research can embed text with Voyage AI or OpenAI (`--embedder voyage|openai`). This
sends data to that provider:
- **Ingestion** sends every chunk of the corpus. This is an operator command, and the CLI help
  says so.
- **Search** sends the query text. `find_related_signals` then declares the `network`
  permission. It runs only when the operator grants `network`
  (`QUANT_AGENT_OS_GRANTED_PERMISSIONS`) and either approves each call or pre-approves
  `network` (`QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`). The default policy refuses it.

Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` (or the `QUANT_RESEARCH_`-prefixed
variants) and are held as `SecretStr`. Error messages name the variable, never its value. Use
the offline `hashing` embedder, or none, for documents that must not leave the machine.

## Serving is read-only

`quant-research serve-mcp` and `quant-research search` open the store with SQLite's
`mode=ro`. They create, migrate, and write nothing, so a read-only mount works. A store at
another schema version is refused with the command that upgrades it. Each answer reads inside
one transaction, so it never mixes two versions of the corpus. Hosted query embedding is
bounded to 8 seconds inside the 15-second tool timeout; past that, the vector channel is
skipped with a warning. Repeated queries are served from a cache rather than sent to the
provider again.

## Run records

`serve-mcp` records every call as a run in `runs.db` beside the knowledge store. Pass
`--runs PATH` to put it elsewhere, or `--no-record` to keep none. The SDK `Platform` keeps runs
in memory unless it is given a run store. What each part of a record holds:
- **Calls.** A call record holds the tool's arguments and output, which for research tools
  includes retrieved passages. Treat `runs.db` as being as sensitive as the corpus.
- **Redaction.** Before anything is stored, `SecretStr` values and values under keys that
  name a secret (`api_key`, `token`, `password`, ...) are replaced with `[REDACTED]`.
- **Audit events.** These hold only ids, hashes, and statuses, and the database rejects any
  update or deletion of them.
- **Store failures.** If the run store cannot be opened, serving continues unrecorded with a
  warning, and a failed write is logged. Losing an answer the tool already produced would not
  make the record more complete.

## REST interface

[Decision 0011](decisions/0011-rest-api-and-api-keys.md). `quant-research serve-api` serves the
same tools as `serve-mcp` over HTTP, which other machines may reach, so:
- **Every route but `/healthz` needs an API key.** Send it as `Authorization: Bearer <key>`.
  `quant-agent-os api-key new` prints a key once. The keys file
  (`~/.quant-agent-os/api-keys.toml` or `QUANT_AGENT_OS_API_KEYS_FILE`) holds only SHA-256
  hashes and is created with mode 0600. To revoke a key, delete its entry or set
  `disabled = true`, then restart the server. The server does not start without an enabled key.
- **Scopes narrow; they never widen.**
  - `tools:list` lists the tools and `tools:call` calls them.
  - `runs:read` reads every run in the store, including retrieved passages, so grant it like
    access to the corpus.
  - The permission policy is the same as `serve-mcp`'s, read-only by default.
  - There is no approval route. A consequential call returns `approval_required`, and the
    operator approves it through the SDK on the same run store.
- **Every call is audited.** Each call is a run with actor `rest` and the key's principal, on
  the run and on every audit event. The server does not start if the run store cannot be
  opened.
- **Exposure.**
  - The server binds `127.0.0.1` by default and warns when bound anywhere else.
  - Terminate TLS, and rate-limit, in a reverse proxy.
  - There are no public schema pages, no CORS, and no server header.
  - Request bodies are capped at 5 MiB, and error messages never echo the key.

## MCP over HTTP

[Decision 0012](decisions/0012-remote-mcp-over-http.md). `quant-platform serve-http` serves MCP
at `/mcp` beside the REST routes, with the same keys, policy, and run store:
- **Every MCP request needs an API key.** A missing or unknown key gets 401 with
  `WWW-Authenticate`. The key is checked through the SDK's token-verifier interface, where
  OAuth tokens will plug in (plan step P5).
- **Each method checks the key's scope.** Listing tools, resource templates, and prompts, and
  getting a prompt, need `tools:list`; calling a tool and reading a resource (which calls a
  tool) need `tools:call`. There are no new scopes, and no MCP method can approve a call.
- **Stateless.** Each request stands alone: no session ids to steal, and nothing lost on a
  restart.
- **DNS-rebinding protection.** Only loopback and the `--allowed-host` names are accepted as
  `Host` or `Origin`; browsers on other origins are refused.
- **Audited.** Each call or read is a run with actor `mcp-http` and the key's principal.
- **Same limits as REST.** It binds loopback by default, caps bodies at 5 MiB, has no CORS, and
  never logs or echoes a key (contract test). It has no rate limiting yet, so put a
  rate-limiting proxy in front of any non-loopback deployment.

## PostgreSQL knowledge store

[Decision 0004](decisions/0004-knowledge-store.md).
- **Passwords.** Give the password through `PGPASSWORD` or `~/.pgpass`, not in the
  `postgresql://` URL. A URL is easy to log, and it shows in process lists. Every message
  shows the URL with any password replaced by `***`.
- **Read-only serving.** Serving opens the store in read-only transactions. Defend in depth
  with a role that can only `SELECT`; `deploy/compose.postgres.yaml` creates one, and CI checks
  that it is refused writes by permission.
- **Its own schema.** The store keeps to its own schema. It creates pgvector only when the
  role is allowed to; otherwise the vector channel is unavailable.

## Docker team server

[docs/deployment.md](deployment.md). The image holds code only. The knowledge store, API-key
hashes, and run store are volumes, and `.dockerignore` keeps `.env` files and databases out of
the build context.

The compose file applies these settings to the server:
- it runs as uid 10001, with a read-only root filesystem, no capabilities, and
  `no-new-privileges`;
- the knowledge store and keys are mounted read-only;
- it publishes only on `127.0.0.1`.

The operator's `ingest` and `keys` commands run with no network. CI checks the image for
data files, key files, and secret-looking environment variables on every pull request.

## Approvals for consequential permissions

[Decision 0005](decisions/0005-approval-gates.md) treats `write_local_data`, `execute_code`,
`network`, and `external_action` as consequential:
- **Granting is only eligibility.** Granting one of these permissions makes a tool eligible to
  run, nothing more.
- **Each call needs an approval.** The approval is single-use and bound to the run id, the tool
  name, and the input hash.
  - Without one, the call records `approval_required`, runs nothing, and the run waits in
    `awaiting_approval`.
  - The operator approves through the SDK (`Platform.approve`, then `Platform.resume`) or by
    confirming `quant-factory create`.
  - Models, MCP clients, and REST clients can never create approvals.
- **Standing approvals.** An operator can pre-approve a permission for every call with
  `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`. It is empty by default, and every call that uses
  it records `standing:<permission>`.
- **Running programs.** Programs run through `run_command`:
  - a fixed working directory and no stdin;
  - an allowlisted environment: `PATH`, home, temp, locale, uv caches, and certificates. API
    keys, `QUANT_AGENT_OS_*` settings, and package-index URLs (which can embed credentials)
    never pass through;
  - a timeout that kills the whole process tree.
- **App Factory checks.** The App Factory runs only `uv` lifecycle commands in a generated
  project. Any other program is reported and skipped.
- **Ingestion.** Knowledge-store ingestion is an operator command, not an agent tool.

## Secret references

[Decisions 0007 and 0008](decisions/0008-factory-settings.md). App specs, the factory
settings file, and generated apps hold environment-variable names, never secret values:
- **Refused values.** A value that looks like a secret, under a secret-named key or in a
  setting declared secret, is refused.
- **Generated files.** They name variables (`.env.example`, `settings.py`) and hold no values;
  a scan test checks this.
- **Programs.** By default, programs receive no secrets. The one exception is a generated
  app's `check-connection` command:
  - it receives only the variables the spec references, through `run_command`'s `extra_env`;
  - it runs during the operator's approved create, or an approved `quant-factory check`;
  - it runs only for projects from built-in templates, whose connection code App Factory
    ships;
  - the values, including URL-encoded forms, are redacted from its captured output.
- **Database TLS.** Generated PostgreSQL connections verify the server certificate
  (`sslmode=verify-full`) unless the spec explicitly chooses a weaker mode.

Generated database apps hold no credentials. They read `DATABASE_URL` or `DATABASE_PASSWORD`
from their environment and open connections read-only by default: SQLite query-only, DuckDB
read-only files, and PostgreSQL read-only transactions. A scan test checks that no secret
reaches the generated files.

## Out of scope for the first release

- autonomous trading or brokerage connectivity;
- unrestricted code execution;
- high-impact external actions;
- hosted multi-tenant operation.

## Reporting a vulnerability

Do not open a public issue. Contact the maintainer privately through the repository's security
advisory feature.
