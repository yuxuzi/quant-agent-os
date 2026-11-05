# Team server: the research and analytics toolsets over MCP and REST, in Docker

This guide runs the Quant-Agent-OS platform as a small team server. Each teammate adds it to
Claude, Cursor, or Copilot as a remote MCP server; applications call the same tools over REST.
Both need an API key. Every call is recorded with the caller's name, and nobody but the
operator can change the corpus or the datasets.

What runs:
- **One container** runs `quant-platform serve-http`: MCP at `/mcp` and REST at `/v1`, on one
  port, with one set of keys ([decisions 0011](decisions/0011-rest-api-and-api-keys.md) and
  [0012](decisions/0012-remote-mcp-over-http.md)).
- **Four volumes** hold the data. `knowledge` holds the SQLite knowledge store, `datasets` the
  CSV datasets the analytics tools read by id, `keys` the API-key hashes, and `runs` the run
  store and audit trail.
- **The image holds code only.** It contains no corpus, keys, runs, or secrets.

There are two variants, and they give the same answers for the same corpus:
- **SQLite** (`deploy/compose.yaml`) is the simplest. It is enough for one team, because
  serving only reads the store, and the corpus changes only when the operator runs `ingest`.
- **PostgreSQL with pgvector** (`deploy/compose.postgres.yaml`) suits larger corpora, existing
  database operations, and backups. See [PostgreSQL](#postgresql) below. The same steps apply.

Everything below runs from the repository root. You need Docker with Compose v2.

## 1. Build

```bash
docker compose -f deploy/compose.yaml build
```

The build installs the locked dependencies (`uv sync --locked`) for the platform server with
its research and analytics toolsets and the `pdf` and `postgres` extras, and nothing else: no
dev tools, and no App Factory, which is an optional extra (`quant-agent-os-platform[factory]`).
It uses a pinned Python 3.13 base image and a pinned uv, both by digest.

## 2. Ingest your corpus (operator)

```bash
CORPUS=/path/to/notes-and-papers docker compose -f deploy/compose.yaml run --rm ingest
```

`CORPUS` is mounted read-only. Without `CORPUS`, the sample corpus is ingested. Ingestion runs
with no network, so hosted embedders are not available here. Run it again whenever the corpus
changes: unchanged files are skipped by content hash, and the server sees the new snapshot on
its next request.

## 3. Issue API keys (operator)

```bash
docker compose -f deploy/compose.yaml run --rm -T keys new --principal alice \
  --scope tools:list --scope tools:call
```

- **The key is printed once.** Give it to Alice over a channel you trust. Only its SHA-256
  hash is stored, in the `keys` volume.
- **Scopes:**
  - `tools:list` and `tools:call` list and call the tools;
  - `runs:read` reads every recorded run, including retrieved passages, so grant it like
    access to the corpus.
- **Revoking:** remove the key's entry, or add `disabled = true` under it, in
  `/keys/api-keys.toml`, then restart the server.
- **New keys** are read when the server starts, so restart it after issuing one:
  `docker compose -f deploy/compose.yaml restart server`.

## 4. Add datasets (operator)

```bash
DATASETS_SOURCE=/path/to/csvs docker compose -f deploy/compose.yaml run --rm datasets \
  push /import/returns.csv --id returns
```

A dataset is a CSV file: a header row, an index column (dates, oldest first), then numeric
columns, where blank and `NaN` cells count as missing. The first column is the index when it is
named `date`, `datetime`, `time`, or `period`, holds `20240102` or `202401` values, or is not all
numbers; `--index` names any other column. The tools infer the frequency from the dates and join
datasets on them. The analytics tools take `{"dataset": "returns", "column": "fund"}` instead of the
values, so the numbers never pass through a model. Without `DATASETS_SOURCE`, the import
directory is `deploy/sample-data`. Pushing the same id again replaces the dataset; its new
fingerprint shows in each result's data versions. The server reads datasets per request, so no
restart is needed.

## 5. Start the server

```bash
docker compose -f deploy/compose.yaml up -d server
curl http://127.0.0.1:8000/healthz      # {"status":"ok"}
```

The container runs as an unprivileged user (uid 10001):
- the root filesystem is read-only, all capabilities are dropped, and privilege escalation is
  blocked;
- the knowledge store and keys are mounted read-only, and only the run store is writable;
- the port is published on `127.0.0.1` only. The server's warning about binding `0.0.0.0`
  refers to its address inside the container.

Add it to Claude Code as a remote MCP server:

```bash
claude mcp add --transport http quant-agent-os http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $KEY"
```

Or call a tool over REST:

```bash
curl -s -H "Authorization: Bearer $KEY" -X POST \
  http://127.0.0.1:8000/v1/tools/find_related_signals/call \
  -H 'content-type: application/json' -d '{"arguments": {"query": "short-term reversal"}}'
```

MCP is stateless with JSON responses; each MCP method checks the key's scope (`tools:list` to
list, `tools:call` to call a tool or read a resource). The REST routes, status codes, and scopes
are in [decision 0011](decisions/0011-rest-api-and-api-keys.md). MCP, REST, and stdio results
are equal for the same arguments.

## 6. See who called what (operator)

```bash
docker compose -f deploy/compose.yaml run --rm runs            # recent runs
docker compose -f deploy/compose.yaml run --rm runs <run-id>   # one run, its calls, its audit
```

REST runs show actor `rest`, MCP runs actor `mcp-http`, each with the key's principal. Audit
events are append-only.

Every recorded call also has a usage event ([decision 0015](decisions/0015-metering-quotas-and-entitlements.md)):
its tenant, tool, toolset, status, duration, and input and output sizes, never its arguments or
output. Export it per call or as totals:

```bash
docker compose -f deploy/compose.yaml run --rm usage --tenant acme --by day   # this month, CSV
docker compose -f deploy/compose.yaml run --rm usage --from 2026-11-01 --to 2026-12-01 \
    --by tool --format jsonl
```

Calls that ended `ok`, `error`, or `timeout` are billable; refusals and invalid input are
recorded but not billable. Without a run store (`serve-mcp --no-record`) nothing is metered.

## 7. Expose it to your team

Keep the published port on loopback, and put a reverse proxy in front. The proxy should:
- terminate TLS;
- apply rate limits (the server has none yet; quotas come with plan step P7);
- forward the `Authorization` header unchanged.

MCP refuses a `Host` or `Origin` it does not know (DNS-rebinding protection). Add the public
name clients use to the server's command: `--allowed-host research.example.com`.

The server sends no CORS headers, so browsers on other origins cannot call it directly. Back
up the `knowledge`, `keys`, and `runs` volumes. A lost `knowledge` volume can be rebuilt by
ingesting again; the `runs` volume is your audit trail.

## Many tenants (decision 0013)

One server can serve several teams or customers, each with its own corpus, datasets, toolsets,
and permissions:

```bash
quant-platform tenant add acme --store postgresql://qaos_reader@postgres/qaos \
  --toolsets research,analytics --issuer https://login.acme.example
quant-research ingest /corpus/acme --store postgresql://qaos_ingest@postgres/qaos --schema qr_acme
quant-agent-os dataset push returns.csv --id returns --tenant acme
quant-agent-os api-key new --principal acme-bot --scope tools:list --scope tools:call --tenant acme
quant-platform serve-http --tenants ~/.quant-agent-os/tenants.toml --host 0.0.0.0
```

- **The tenants file** (`--tenants`, or `QUANT_AGENT_OS_TENANTS_FILE`) lists each tenant's
  store, schema, datasets directory, toolsets, permissions, and pinned issuer.
  `quant-platform tenant list` shows it with passwords hidden, and `tenant disable` stops
  serving a tenant at the next restart while keeping its data.
- **On PostgreSQL** each tenant's corpus is its own schema, `qr_<tenant>` by default. The
  roles that `deploy/postgres/init-roles.sh` creates already let the server's read-only role
  read every schema that ingestion creates.
- **In Docker**, mount the tenants file and override the server's command with
  `--tenants /config/tenants.toml`. Each tenant's datasets go in `/data/datasets/<tenant>`.

**OAuth for connectors.** For Claude or Cursor connectors that sign in through a tenant's
identity provider, list the provider in the tenants file. Then start the server with the
public URL of `/mcp`, which tokens must name as their audience:

```toml
[[authorization_servers]]
issuer = "https://login.acme.example"
introspection_url = "https://login.acme.example/oauth2/introspect"
client_id = "qaos"
client_secret_env = "ACME_INTROSPECTION_SECRET"   # set this variable on the server
```

```bash
ACME_INTROSPECTION_SECRET=... quant-platform serve-http --tenants tenants.toml \
  --resource-url https://qaos.example.com/mcp --allowed-host qaos.example.com
```

The server publishes `/.well-known/oauth-protected-resource/mcp`, and its 401 responses point
there, so a client finds the provider by itself. In the provider, register the server as a
resource with that audience. Its tokens must carry the scopes (`tools:list`, `tools:call`) and
a `qaos_tenant` claim naming the tenant; `tenant_claim` in the file changes the claim name.

## Plans and limits (decision 0015)

A plan bounds what its tenants get: the toolsets they may be served, a rate limit, a monthly
quota, and an output cap. Plans live in the tenants file, and a tenant names its plan:

```toml
[plans.team]
toolsets = ["research", "analytics"]
calls_per_minute = 120        # per tenant, per server process
monthly_calls = 50_000        # billable calls per UTC calendar month
max_output_bytes = 200_000    # lowers any tool's own output limit

[tenants.acme]
plan = "team"
toolsets = ["research"]       # optional: narrows the plan, never widens it
```

```bash
quant-platform tenant add acme --store postgresql://qaos_reader@postgres/qaos --plan team
```

- **No plan, no limits.** A tenant without a plan is served as before.
- **Out-of-plan toolsets are not listed.** A tenant's tools, resources, and prompts come only
  from its entitled toolsets, so a client never sees a tool it may not call.
- **Limits are checked before a call starts.** A call that starts always finishes, even if it
  takes the tenant over its quota. A refused call records `quota_exceeded`, which is not
  billable, with `retry_after_seconds`; REST answers it 429 with `Retry-After`.
- **The monthly count** comes from the usage events in the run store, re-read every 30
  seconds. The rate limit is a token bucket in the server process, so N replicas would admit
  up to N times the rate.
- **The start-up check** refuses an unknown plan, a tenant that widens its plan, and the
  reserved `packs` key (knowledge packs are later work).

## Approving in the client (decision 0014)

Tools that need a consequential permission (for example, a tenant granted `network` for a
hosted embedder) wait for an approval. To let people approve them without leaving their
client, give the server its public base URL and issue approver keys:

```bash
quant-agent-os api-key new --principal lead --scope runs:approve --tenant acme
quant-platform serve-http --tenants tenants.toml --approval-url https://qaos.example.com
```

The client then shows a link to `https://qaos.example.com/approve/<id>`. There, an approver
enters their key, checks the tool, arguments, and permissions, and approves once. The client
runs the same call again, and it runs once. An approver key cannot call tools, and outside the
`local` tenant nobody approves their own request (set `separate_approver = false` on a tenant
to allow it). Requests expire after 15 minutes. Five failed submissions deny a request, and
each client address may submit 30 forms a minute. The server trusts `X-Forwarded-For` only from
`127.0.0.1`; behind a reverse proxy on another address, set `FORWARDED_ALLOW_IPS` to the
proxy's address, or every client shares the proxy's allowance. Never set it to `*`, which lets
any client choose its own address.

## Telemetry and service levels (decision 0015)

Every tool call emits an OpenTelemetry span and two metrics, labelled with the tenant, the
tool, and the call's status, never its arguments, output, caller, or key:

| Signal | Name | Prometheus name |
| --- | --- | --- |
| Span | `execute_tool <tool>` (ERROR for `error` and `timeout`) | — |
| Counter | `qaos.tool.calls` | `qaos_tool_calls_total` |
| Histogram, seconds | `qaos.tool.duration` | `qaos_tool_duration_seconds` |

The server uses only the OpenTelemetry API, which does nothing until an SDK is configured. To
export, add the SDK and an exporter and start the server under `opentelemetry-instrument`:

```bash
OTEL_SERVICE_NAME=quant-agent-os OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 \
  uv run --with opentelemetry-distro --with opentelemetry-exporter-otlp \
  opentelemetry-instrument quant-platform serve-http --tenants tenants.toml
```

`opentelemetry-instrument` also traces the HTTP libraries it finds, and those spans record
request paths, including approval-page request ids. Send telemetry only to a collector you
trust with that, or turn those libraries off with `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`.

Two service-level indicators, per tenant and tool:
- **Availability.** The share of calls that did not end `error` or `timeout`. Refusals
  (`denied`, `approval_required`, `quota_exceeded`, `invalid_input`) are the server working as
  intended, so they count as good.
- **Latency.** The 95th percentile of `qaos.tool.duration` per tool. Search with a hosted
  embedder is bounded by its 15-second timeout; analytics calls take milliseconds.

[deploy/observability/qaos-slo-dashboard.json](../deploy/observability/qaos-slo-dashboard.json)
is an example Grafana dashboard over Prometheus with these indicators, a 99.5% availability
objective over 30 days, calls by status, and refusals by tenant. Choose your own objectives.

## Permissions

The server is read-only by default. The tools that need more (for example `network`, for a
hosted embedder) are refused unless the operator sets `QUANT_AGENT_OS_GRANTED_PERMISSIONS` on
the `server` service. Consequential permissions also need a per-call approval, which no API
key can give ([security](security.md)).

## PostgreSQL

Set three passwords, then use `compose.postgres.yaml` in every command above:

```bash
cp deploy/.env.example deploy/.env       # never committed; set each password (openssl rand -hex 24)
docker compose -f deploy/compose.postgres.yaml build
CORPUS=/path/to/notes docker compose -f deploy/compose.postgres.yaml run --rm ingest
docker compose -f deploy/compose.postgres.yaml run --rm -T keys new --principal alice \
  --scope tools:list --scope tools:call
docker compose -f deploy/compose.postgres.yaml up -d server
```

What changes:
- **PostgreSQL** runs from `pgvector/pgvector:0.8.6-pg17`, pinned by digest, on an internal
  network with no published port and no internet access. Its data is in the `pgdata` volume.
- **Two database roles.** On first start, `deploy/postgres/init-roles.sh` creates pgvector and
  two roles:
  - `qaos_ingest` owns the `quant_research` schema, and `ingest` connects as it;
  - `qaos_reader` can only `SELECT`, and its transactions are read-only. The server connects
    as this role, so even a compromised server cannot change the corpus.
- **Passwords** reach the containers as `PGPASSWORD`, from `deploy/.env`. Anyone who can run
  `docker inspect` on the host can read them, so restrict access to the Docker host.
- **The run store** stays a SQLite file in the `runs` volume.

To use an existing PostgreSQL server instead, give the server and `ingest` its URLs
(`--store postgresql://user@host/db`) and passwords. Create an equivalent read-only role for
the server.

## Check it yourself

`deploy/smoke-test.sh` is what CI runs on every pull request, for both variants. It:
- builds the image and checks that it runs as non-root and holds no data, keys, or
  secret-looking environment variables;
- ingests the sample corpus, issues a key, adds the sample dataset, and runs
  `docker compose up`;
- checks health, a 401 without a key, a real REST call, and the principal on the recorded run;
- checks MCP at `/mcp`: a 401 without a key, a foreign `Host` refused, the tool list, a
  `research_report` call with metrics from the dataset, and the call recorded as `mcp-http`;
- checks that the server cannot write its knowledge store.

It uses its own compose project and port 18000, and it removes its volumes afterwards. On
PostgreSQL it also checks that the server's role is refused writes by permission, with
throwaway passwords.

```bash
deploy/smoke-test.sh            # SQLite
deploy/smoke-test.sh postgres   # PostgreSQL with pgvector
```
