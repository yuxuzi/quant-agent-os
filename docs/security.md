# Security model

Quant-Agent-OS assumes that model output, retrieved documents, and tool output are untrusted.
The [security review](security-review.md) checks the remote surface against this model and
lists the open findings. This project holds no certification and claims compliance with no standard. It has had
an AI agent's review and no independent one, and an independent review is recommended before production use.

## What crosses which boundary

```text
 Your people                        Your firm's network                         Outside, if you choose
┌────────────────┐  TLS  ┌───────┐ HTTP  ┌─────────────────────────────┐
│ AI client      ├──────▶│ your  ├──────▶│ qaos server (container)     │
│ (Claude,       │ API   │ proxy │       │  keys ▶ access ▶ executor   │
│  Cursor, ...)  │ key   └───────┘       │  ├ platform tools           │
└───────┬────────┘                       │  └ your tools (your code)   ├──▶ hosted embedder: query text,
        │ every result it reads          └──┬──────────┬───────────────┘    only if you grant `network`
        ▼                                   │ reads    │ records
 The client's model, which you              ▼          ▼
 and your client's vendor control     corpus, data    runs, audit        ┌──────────────────────┐
                                      keys, access    usage              │ Operators: `qaos ...`│
                                      (read-only)     (read-write)  ◀────┤ on the host. Trusted │
                                                                         └──────────────────────┘
```

- **The server checks the API key, then the tool's permissions, then the caller's access.** Everything to the left
  of the executor is untrusted input, including what the model asks for.
- **Results reach the client, and so its model.** Keeping numbers out of the model through dataset references covers
  your input series. Metrics, passages, and report text are the answer, so they go to the client and whichever model it
  uses. Choose a client and a model provider your data may go to. This is the largest data flow, and the platform
  cannot limit it.
- **Your tools are server code.** See [the limits](#what-this-does-not-protect-against).
- **Operators are outside the model.** Whoever can run `docker compose` on the host can read every volume and change
  anything.

## Who is responsible for what

The platform is software you run. It has no service of ours behind it, so nothing here reaches back to its authors.

| Area | The platform | Your firm |
| --- | --- | --- |
| **TLS** | Serves plain HTTP, on loopback by default, and warns otherwise | Terminates TLS in a reverse proxy, and manages certificates |
| **Rate limits** | Bounds each call, and 16 calls at once | Limits requests in the proxy, including unauthenticated ones |
| **Identity** | Checks API keys, kept as hashes, with expiry and revocation that apply with no restart | Decides who holds a key, hands keys over, and removes leavers. There is no single sign-on on `qaos serve` |
| **Authorization** | Applies an access file or your `AccessPolicy` to tools and datasets. Deny by default once configured | Decides the rules, and enforces anything finer in its own tools |
| **Secrets** | Stores none in the image or in generated files, and redacts secret-named values from records | Stores `deploy/.env` and provider keys, and rotates them ([how](operations.md#where-things-live)) |
| **Backups** | Provides `backup`, `restore`, and a tested [upgrade and rollback](operations.md#roll-back) | Runs them on a schedule, stores and encrypts them, and tests a restore |
| **Retention** | Provides [`qaos runs prune` and `export`](operations.md#retention-and-export). Removes nothing unasked | Sets the policy, and runs it |
| **Network** | Publishes on loopback. PostgreSQL has an internal network, and operator commands have none | Controls what reaches the host, and what the server may reach |
| **Patching** | Pins dependencies and base images, and publishes fixes in releases | Applies releases and the host's patches, and rebuilds images |
| **Your tools** | Validates their input and output, bounds and records each call | Reviews them as server code, and tests them with `check_tool` |
| **Monitoring and incidents** | Records every call, and emits telemetry with no content | Watches it, responds to incidents, and tells anyone it must. Report platform flaws [privately](#reporting-a-vulnerability) |
| **Compliance** | Makes no claim | Decides what its rules require, including how long it keeps records |

## What is stored where

| Kind of data | Sensitivity | Where it lives | Who can reach it |
| --- | --- | --- | --- |
| Research corpus: text, entities, embeddings | Your research | `knowledge` volume, or PostgreSQL | Any caller allowed a research tool, the whole corpus |
| Datasets | Your data | `datasets` volume, or your own store | Callers an access file or your `AccessPolicy` allows. Without either, any key with `tools:call` |
| Runs and calls: arguments, outputs, **retrieved passages,** computed results | As sensitive as the corpus and data together | `runs` volume | Any key with `runs:read`, for **every** caller's runs in its tenant. `qaos serve` has one tenant |
| Audit events: ids, hashes, statuses, principals | Who did what, with no content | `runs` volume, kept forever | Any key with `runs:read`, in its tenant |
| Usage events: tool, status, duration, sizes | Activity, with no content | `runs` volume, kept forever | Operators |
| API-key hashes, with principals and groups | Access map | `keys` volume, mode 0600 | The server, read-only, and operators |
| Access rules | Access map | Your file, mounted read-only | The server, and operators |
| Database passwords | Secret | `deploy/.env`, and the containers' environment | Anyone who can `docker inspect` on the host |
| Provider keys, if you use a hosted embedder | Secret | The environment, or the home's `.env` | The server. Never in records or errors |
| Logs | Names of principals, tools, and refused datasets. Never keys or arguments | Container logs | Operators |
| Telemetry | Tenant, tool, status, duration. Automatic HTTP spans also hold request paths | Your collector, off until you add one | Whoever runs it |
| Backups and exports | Backups hold everything above but `deploy/.env`. Audit exports hold no content | Wherever you put them | Whoever you let. Pruning does not reach them |
| Acceptance reports | Numbers and source names, never retrieved text | Wherever you put them | Whoever you let |

## What this does not protect against

- **A tool can do more than it declares.** The executor checks a tool's declared permissions, and nothing sandboxes
  it. A tool that declares only `read_local_data` can still open a file or a socket, because it is Python in the
  server's process (tried, and [finding F-6](security-review.md#findings)). So your tools are as trusted as the server,
  and can read what it can, including the runs and every dataset. Review them like server code, and limit the
  container's network if they need none.
- **The corpus is all or nothing.** A caller allowed a research tool searches the whole corpus. For research that two
  groups must not share, run one server per corpus.
- **`runs:read` sees everyone in its tenant.** It reads every caller's runs in the key's tenant, which on `qaos serve`
  is every caller, with what they retrieved and computed. Give it only to auditors, and never to a key that a client's
  model can use.
- **The audit trail resists accidents, not its operator.** The database rejects changes to audit events, but whoever
  can write the file can remove that rule. For evidence that must outlast a compromised host,
  [export it](operations.md#retention-and-export) to a system they do not control.
- **A client can be talked into things.** A passage can hold instructions. Results are marked untrusted, and the server
  says not to follow them, but only the client decides. Consequential calls still stop for a person's approval, unless
  an operator gave that permission a standing approval with `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS`, which is empty by
  default and recorded on each use.
- **Local use trusts whoever starts it.** stdio and the SDK have no authenticated caller, and apply no access rules.
- **No sign-in through an identity provider yet.** OAuth token checks exist in `quant_platform.identity`, but no
  command serves them, and tokens carry no groups.
- **Nothing limits unauthenticated requests.** That is the proxy's job.

## Defaults

- **Read-only.** The default policy grants only `read_local_data`. Tools that need `network`,
  `write_local_data`, `execute_code`, or `external_action` are refused unless the operator grants
  those permissions explicitly.
- **Explicit tools.** Only tools registered in a `ToolRegistry` can run, optionally narrowed
  further by an allowlist. There is no dynamic discovery or code loading.
- **Bounded calls.** Every call validates its input and output, has a timeout, and has an
  output-size limit. A timed-out Python thread cannot be killed, so tools must avoid unbounded
  or side-effecting work.
- **Retrieved text never steers the platform.** The platform has no planner and calls no model,
  so no platform code reads tool output or retrieved text to choose what runs next.
- **Clients plan for themselves.** An MCP or REST client such as Claude or Cursor
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

Keys come from `VOYAGE_API_KEY` or `OPENAI_API_KEY` and are held as `SecretStr`. Error messages name the variable, never its value. Use
the `local` embedder, or none, for documents that must not leave the machine. It sends no text
anywhere. It downloads its model files from Hugging Face on first use, into `models/` in the home.

## Serving is read-only

`qaos serve --toolsets research` and `qaos search` open the store with SQLite's
`mode=ro`. They create, migrate, and write nothing, so a read-only mount works. A store at
another schema version is refused with the command that upgrades it. Each answer reads inside
one transaction, so it never mixes two versions of the corpus. Hosted query embedding is
bounded to 8 seconds inside the 15-second tool timeout; past that, the vector channel is
skipped with a warning. Repeated queries are served from a cache rather than sent to the
provider again.

## Run records

`qaos serve` records every call as a run in `runs.db` in the home. Pass
`--runs PATH` to put it elsewhere, or `--no-record` to keep none. The SDK `Platform` keeps runs
in memory unless it is given a run store. What each part of a record holds:
- **Calls.** A call record holds the tool's arguments and output, which for research tools
  includes retrieved passages. Treat `runs.db` as being as sensitive as the corpus.
- **Redaction.** Before anything is stored, `SecretStr` values and values under keys that
  name a secret (`api_key`, `token`, `password`, ...) are replaced with `[REDACTED]`.
- **Audit events.** These hold only ids, hashes, and statuses, and the database rejects any
  update or deletion of them.
- **Retention.** Runs are kept until an operator runs `qaos runs prune`, which removes ended runs and their calls,
  overwrites the freed space, and records itself. Audit and usage events stay. `qaos runs export` gives each call and
  audit event without arguments or outputs. Both are operator commands, not tools ([operations](operations.md#retention-and-export)).
- **Usage events.** One per recorded call, written in the same transaction
  ([decision 0015](decisions/0015-metering-quotas-and-entitlements.md)). They hold the tenant,
  principal, tool, status, duration, and the sizes of the arguments and output, never their
  content. No tool reads them.
- **Store failures.** If the run store cannot be opened, serving continues unrecorded with a
  warning, and a failed write is logged. Losing an answer the tool already produced would not
  make the record more complete.

## REST interface

[Decision 0011](decisions/0011-rest-api-and-api-keys.md). `qaos serve --http --toolsets research` serves the
same tools as `qaos serve` over HTTP, which other machines may reach, so:
- **Every route but `/healthz` needs an API key.** Send it as `Authorization: Bearer <key>`.
  `qaos keys new` prints a key once. The keys file
  (`~/.quant-agent-os/api-keys.toml` or `QUANT_AGENT_OS_API_KEYS_FILE`) holds only SHA-256
  hashes and is created with mode 0600. To revoke a key, delete its entry or set
  `disabled = true`. The server re-reads the file when it changes, and accepts no key while the
  file is missing or invalid. A key can have an `expires` time (`--expires-in-days`). The server
  does not start without an enabled, unexpired key.
- **Scopes narrow; they never widen.**
  - `tools:list` lists the tools and `tools:call` calls them.
  - `runs:read` reads every run in the store, including retrieved passages, so grant it like
    access to the corpus.
  - The permission policy is the same as `qaos serve`'s, read-only by default.
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

[Decision 0012](decisions/0012-remote-mcp-over-http.md). `qaos serve --http` serves MCP
at `/mcp` beside the REST routes, with the same keys, policy, and run store:
- **Every MCP request needs an API key.** A missing or unknown key gets 401 with
  `WWW-Authenticate`. The key is checked through the SDK's token-verifier interface, which
  is also how OAuth tokens would be checked.
- **Each method checks the key's scope.** Listing tools, resource templates, and prompts, and
  getting a prompt, need `tools:list`; calling a tool and reading a resource (which calls a
  tool) need `tools:call`. There are no new scopes, and no MCP method can approve a call.
- **Stateless.** Each request stands alone: no session ids to steal, and nothing lost on a
  restart.
- **DNS-rebinding protection.** Only loopback and the `--allowed-host` names are accepted as
  `Host` or `Origin`; browsers on other origins are refused.
- **Audited.** Each call or read is a run with actor `mcp-http` and the key's principal.
- **Same limits as REST.** It binds loopback by default, caps bodies at 5 MiB, has no CORS, and
  never logs or echoes a key (contract test). Nothing limits a key's call rate or unauthenticated
  requests, so put a rate-limiting proxy in front of any non-loopback deployment.

## Per-caller access

Permissions say what the server may do at all. An access policy says what one caller may do: which tools it
may call and which datasets it may read ([decision 0021](decisions/0021-per-caller-access.md)). It is off until
configured, and then it denies what is not listed. How to use it, and what it does not cover, is in
[deployment](deployment.md#9-decide-who-reaches-what).

- The caller comes only from the verified credential, never from a request.
- Anything uncertain is a refusal: no bound caller, a policy that raises or returns anything but `True`, or an
  access file that has become missing or invalid.
- A refused dataset cannot be told from a missing one, and no output names what the caller may not reach. The
  refusal is on the call's record and audit event, which callers do not receive.
- Configurations that would silently not apply are called out. On `serve_http`, keys with groups and no access policy,
  a configured access file with no access policy, or a catalog whose policy is not the configured file each draw a
  warning at start-up, and `qaos doctor` reports groups with no access file.
- `tests/server/test_platform_authorization.py` is a release gate, over MCP and REST.

## OAuth

`quant_platform.identity.oauth` checks tokens from a firm's identity provider by introspection
([decision 0013](decisions/0013-identity-and-tenancy.md)). No command serves it yet; a firm's own server can,
through `build_http_app` ([decision 0023](decisions/0023-no-hosted-service.md)). Each firm runs its own installation, so there are no tenants to
separate.
- **Tokens are checked by introspection** at a configured authorization server. A token is accepted only if it
  is active and unexpired, its audience is this server, and its issuer is pinned. A token is never sent to a
  server that could not have issued it: a JWT's unverified `iss` only chooses which configured server to ask,
  and an opaque token needs exactly one configured server. API keys are never introspected. Results are cached
  for at most 60 seconds, and a failed check refuses the token without logging it.
- **Introspection is bounded**, because anyone can send a token. A provider's refusal is cached for 30 seconds,
  and each provider is asked at most 600 times a minute and 8 times at once; beyond that tokens are refused
  without asking. The reverse proxy's limit on unauthenticated requests stays the first defence.

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

The operator's `ingest`, `keys`, `backup`, and `restore` commands run with no network. CI checks the image for
data files, key files, and secret-looking environment variables on every pull request, and that `doctor`'s output, which
goes in support tickets, holds no secrets.

## Call limits

No command limits a key's calls a minute or a month: plans went with the hosted service
([decision 0023](decisions/0023-no-hosted-service.md)). Each call is still bounded in time, output
size, and concurrency (see [defaults](#defaults)). Limit request rates in the reverse proxy, which
also covers unauthenticated requests. A firm's own server can pass the executor a usage gate
(`ToolExecutor(usage=...)`, [decision 0015](decisions/0015-metering-quotas-and-entitlements.md)),
which refuses a call over its limits with `quota_exceeded` before any approval is used.

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
    confirming `qaos app create`.
  - Models, MCP clients, and REST clients can never create approvals.
- **Approving without leaving the client** ([decision 0014](decisions/0014-approvals-in-the-client.md)):
  - **Over HTTP**, on a firm's own server that adds the approval page from `quant_platform.identity`, a
    waiting call opens an approval request,
    and the client is sent to `/approve/<id>` (MCP URL elicitation). The request is bound to
    the tenant, run, tool, input hash, and requester; its id has 128 random bits; it expires
    after 15 minutes and is decided once.
  - **Only an approver key decides.** The key must have `runs:approve`, belong to the same
    tenant, and (outside the `local` tenant, unless `separate_approver = false`) belong to a
    principal other than the requester. A key can never hold both `runs:approve` and
    `tools:call`, so the key an agent's client holds cannot approve, and OAuth tokens never
    carry `runs:approve`.
  - **The page** shows the tool, permissions, redacted arguments, and input hash. It runs no
    scripts, forbids framing, caching, and referrers, checks a per-form token, and never
    stores or echoes the key.
  - **Guessing is limited** (decision 0015). Five failed submissions (a wrong form token, a
    key that cannot decide, or the requester's own key) deny the request, audited as
    `approval.denied` with reason `too_many_attempts`. Each client address may submit 30
    forms a minute (429 after that). Both counts are kept per server process.
  - **The retried, identical call** runs once, in the original run. A changed argument is a
    new hash and needs a new approval.
  - **Locally**, creating a project is a terminal command, `qaos app create`, whose prompt
    is the approval. No MCP server offers it.
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
  - it runs during the operator's approved create, or an approved `qaos app check`;
  - it runs only for projects from built-in templates, whose connection code App Factory
    ships;
  - the values, including URL-encoded forms, are redacted from its captured output.
- **Database TLS.** Generated PostgreSQL connections verify the server certificate
  (`sslmode=verify-full`) unless the spec explicitly chooses a weaker mode.

Generated database apps hold no credentials. They read `DATABASE_URL` or `DATABASE_PASSWORD`
from their environment and open connections read-only by default: SQLite query-only, DuckDB
read-only files, and PostgreSQL read-only transactions. A scan test checks that no secret
reaches the generated files.

## Out of scope

- autonomous trading or brokerage connectivity;
- unrestricted code execution;
- high-impact external actions.

A public endpoint needs [these steps](security-review.md#before-a-public-endpoint) first.

## Reporting a vulnerability

Do not open a public issue. Contact the maintainer privately through the repository's security
advisory feature.
