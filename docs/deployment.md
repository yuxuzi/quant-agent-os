# Team server: the research and analytics toolsets over MCP and REST, in Docker

This guide runs the Quant-Agent-OS platform as a small team server. Each teammate adds it to
Claude, Cursor, or Copilot as a remote MCP server; applications call the same tools over REST.
Both need an API key. Every call is recorded with the caller's name, and nobody but the
operator can change the corpus or the datasets.

What runs:
- **One container** runs `qaos serve --http`: MCP at `/mcp` and REST at `/v1`, on one
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

Everything below runs from the repository root. You need Docker with Compose v2. With no network on the
server, see [install with no network](operations.md#install-with-no-network).

## 1. Build

```bash
docker compose -f deploy/compose.yaml build
```

The build installs the locked dependencies (`uv sync --locked`) for the server with
its research and analytics toolsets and the `pdf` and `postgres` extras, and nothing else: no
dev tools, and no App Factory, which is an optional extra (`quant-agent-os[factory]`).
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
- **Expiry:** `qaos keys new --expires-in-days 90` sets an `expires` time on the key. You can also
  add one by hand, with a UTC offset: `expires = 2027-01-31T00:00:00Z`.
- **Revoking:** remove the key's entry, or add `disabled = true` under it, in
  `/keys/api-keys.toml`.
- **No restart:** the server re-reads the keys file when it changes, so new, revoked, and edited
  keys apply on the next request. If the file is missing or invalid, the server logs the error and
  **accepts no key** until the file is fixed, so a mistyped revocation can't leave a key working.

## 4. Add datasets (operator)

```bash
DATASETS_SOURCE=/path/to/csvs docker compose -f deploy/compose.yaml run --rm datasets \
  add /import/returns.csv --id returns
```

A dataset is a CSV file: a header row, an index column (dates, oldest first), then numeric
columns, where blank and `NaN` cells count as missing. The first column is the index when it is
named `date`, `datetime`, `time`, or `period`, holds `20240102` or `202401` values, or is not all
numbers; `--index` names any other column. The tools infer the frequency from the dates and join
datasets on them. The analytics tools take `{"dataset": "returns", "column": "fund"}` instead of the
values, so the numbers never pass through a model. Without `DATASETS_SOURCE`, the import
directory is `src/quant_platform/sample/datasets`. Adding the same id again replaces the dataset; its new
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
events are append-only. To send the trail to your own log system, or to stop keeping old runs, see
[retention and export](operations.md#retention-and-export).

## 7. Expose it to your team

Keep the published port on loopback, and put a reverse proxy in front. The proxy should:
- terminate TLS;
- apply rate limits (this server has none; plan quotas are a [hosted](hosted.md) feature);
- forward the `Authorization` header unchanged.

MCP refuses a `Host` or `Origin` it does not know (DNS-rebinding protection). Add the public
name clients use to the server's command, in a compose override: `--allowed-host research.example.com`.

The server sends no CORS headers, so browsers on other origins cannot call it directly.

Before you rely on it, read [operations](operations.md): backups, upgrades, rollback, and what to do
when a store refuses to open.

## 8. Add your own tools

To serve your firm's own Python tools, write a small server of your own, in your repository, that
names them. [examples/firm-tools](../examples/firm-tools/README.md) is a complete one:

```python
from quant_platform import compose, serve_http

catalog = compose(
    policy=settings.policy(),
    research=research.research,
    datasets=store,  # any DatasetStore: a directory, or your own database
    extra_tools=firm_tools(store),  # your tools, named here
    extra_instructions="desk_var gives a desk's value at risk by the firm's convention.",
)
serve_http(catalog, host="0.0.0.0")
```

- **Same controls.** Your tools go through the same executor as the platform's: permissions, input
  and output validation, timeouts, recording, and keys. A tool that declares a permission the
  policy does not grant is refused.
- **No replacing.** A tool with a name the server already serves stops the server at start-up.
- **Your data.** Give `compose` your own `DatasetStore`, which needs only `get` and `ids`. The analytics
  tools and `list_datasets` then read it by reference, and no connector is needed.
- **Test it.** Put `check_tool` from `quant_agent_os.testing` in your tool package's tests.
- **Deploy it.** Build an image from the platform's, as the example's `Dockerfile` does, then run the
  compose file with it: `QAOS_IMAGE=firm-qaos:1 docker compose -f deploy/compose.yaml up -d server`.
  Mount anything your tools read in a compose override. Every other step above stays the same.

The tool names are in your code and not in a setting, so what the server runs is what your repository
says ([decision 0020](decisions/0020-a-firms-own-server.md)).

## 9. Decide who reaches what

By default any key with `tools:call` reaches every tool and dataset the server serves. To give people
different rights, put them in groups and write an access file:

```toml
# access.toml: anything not listed is denied
[groups.growth-desk]
tools = ["analyze_risk", "list_datasets", "find_related_signals", "desk_*"]
datasets = ["growth-*", "factors"]
```

```bash
qaos keys new --principal alice --scope tools:list --scope tools:call --group growth-desk
qaos serve --http --access access.toml    # or set QUANT_AGENT_OS_ACCESS_FILE
```

- **Deny by default.** Once there is an access file, a caller reaches only what its groups list. A key
  with no group reaches nothing, including `server_info` unless a group lists it.
- **A caller sees only its own.** Tool lists, `list_datasets`, `server_info`, and error messages name only
  what the caller may reach. A refused dataset answers exactly as a missing one does.
- **Refusals are recorded.** A refused tool returns `denied`, and a refused dataset returns the same
  error as a missing one. Either way the call's record and its audit event list what was refused, under
  the caller's name: `qaos runs <run-id>`.
- **Groups need the file.** Without an access file, groups do nothing and every key reaches everything. The server warns at
  start-up, and `qaos doctor` warns too.
- **No restart.** A change to the file applies on the next request. If the file becomes missing or invalid,
  **nothing is allowed** until it is fixed, and the server logs why. A server will not start without a valid file.
- **In Docker,** mount the file read-only in a compose override and set `QUANT_AGENT_OS_ACCESS_FILE`.

**The firm's own entitlement system.** Implement `allows(caller, action, resource)`, which is
`AccessPolicy`, and pass it to `compose(access=...)` in your server. If it raises, the call is refused.
Your tools read `current_caller()` for rights the file cannot express, such as one portfolio or account.
Give your tools a `GuardedDatasetStore`, as the [example](../examples/firm-tools/firm_server.py) does,
because a tool holding the unguarded store can read anything in it.

**What this does not do:**
- **The corpus is all or nothing.** A caller who may call a research tool can search the whole corpus,
  and `research_report` reads it too. If two groups must not share research, run a server per corpus.
- **`runs:read` sees every caller's runs,** including what they retrieved and computed. Give it only to auditors.
- **Prompts and server instructions go to every caller.** Keep anything confidential out of `extra_instructions`.
- **Local use is not covered.** stdio and the SDK have no authenticated caller and trust whoever started them,
  so `serve_stdio` refuses a catalog with an access policy. `qaos hosted serve` does not apply an access file, so it
  will not start with one configured.

See [decision 0021](decisions/0021-per-caller-access.md).

## Hosting many customers

This guide is for one firm or team. A vendor serving many customers needs tenants, plans, OAuth,
and the approval page: see [hosted.md](hosted.md).

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
  opentelemetry-instrument qaos hosted serve --tenants tenants.toml
```

`opentelemetry-instrument` also traces the HTTP libraries it finds, and those spans record
request paths, including approval-page request ids. Send telemetry only to a collector you
trust with that, or turn those libraries off with `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`.

Two service-level indicators, per tenant and tool:
- **Availability.** The share of calls that did not end `error`, `timeout`, or `busy`. Refusals
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
- checks that the server cannot write its knowledge store;
- runs `doctor`, checks that its output holds no secrets, runs [agreed test cases](operations.md#check-it-against-agreed-cases),
  then backs up, loses the data, restores it, and finds the recorded run again.

It uses its own compose project and port 18000, and it removes its volumes afterwards. On
PostgreSQL it also checks that the server's role is refused writes by permission, with
throwaway passwords.

```bash
deploy/smoke-test.sh            # SQLite
deploy/smoke-test.sh postgres   # PostgreSQL with pgvector
```

`deploy/upgrade-test.sh` rehearses [operations](operations.md): it builds v0.2.0, seeds it, backs up, upgrades to
this commit, and checks that old runs and answers survive. Then it rolls back, restores, and checks again.
Run it before you trust an upgrade, on either variant, as with the smoke test. It builds two images, so it
takes longer, and it runs by hand rather than on every pull request.

`deploy/firm-image-test.sh` is the same check for [your own tools](#8-add-your-own-tools), and CI runs
it too. It builds the example's image on the platform's, runs it with the compose file's restrictions,
and checks that the firm's tool is listed, called, and recorded under the key's principal.
