# Team server: the research and analytics toolsets over MCP and REST, in Docker

This guide runs the Quant-Agent-OS platform as a small team server. Each teammate adds it to
Claude, Cursor, or Copilot as a remote MCP server; applications call the same tools over REST.
Both need an API key, or, once you [turn on sign-in](#10-sign-in-through-your-identity-provider), a token
from your identity provider. Every call is recorded with the caller's name, and nobody but the
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

**A fund installing from the wheel** into its own Python project, with its own warehouse, identity provider, and
legacy code, follows the [pilot runbook](pilot-runbook.md) instead: the same settings, without Docker, as the
0.8.0 pilot did it.

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
- apply rate limits (this server has none);
- forward the `Authorization` header unchanged.

MCP refuses a `Host` or `Origin` it does not know (DNS-rebinding protection). Add the public
name clients use to the server's command, in a compose override: `--allowed-host research.example.com`.
With [sign-in](#10-sign-in-through-your-identity-provider) on, the resource URL's host is admitted already.

The server sends no CORS headers, so browsers on other origins cannot call it directly.

Before you rely on it, read [operations](operations.md): backups, upgrades, rollback, and what to do
when a store refuses to open.

## 8. Add your own tools

To serve your firm's own Python functions, write a small server of your own, in your repository,
that names them. [examples/firm-tools](../examples/firm-tools/README.md) is a complete one:

```python
from firm_risk.var import desk_var  # your code, unchanged

from quant_agent_os.registration import FirmApp, Series
from quant_platform import serve_apps

risk = FirmApp(
    "risk", instructions="desk_var gives a desk's value at risk by the firm's convention."
)
risk.expose(desk_var, returns=Series, convention="Historical, nearest rank, one period")
serve_apps([risk], datasets=store, host="0.0.0.0")  # store: any DatasetStore
```

- **Schemas from your hints.** `expose` reads the input schema from the type hints, and the description
  and each parameter's description from the docstring (Google, NumPy, or Sphinx style). A function
  without type hints needs them added first. It may return a number, a dict, a dataclass, a Pydantic
  model, or NumPy and pandas values, which are converted.
- **Data by reference.** A parameter marked `Series`, `Frame`, or `Prices` takes a dataset reference,
  such as `{"dataset": "desks", "column": "growth"}`. The server reads the dataset and passes your
  function a list, a NumPy array, or a pandas object, whichever its hint asks for. Missing values arrive
  as NaN, with a warning, and two series from different datasets are not aligned for you.
- **Provenance added.** Every result carries the data's fingerprints, your package's version, the
  convention you state, and `computed` evidence.
- **Same controls.** Your tools go through the same executor as the platform's: permissions, input
  and output validation, timeouts, recording, keys, and the access file. A tool that reads data
  declares `read_local_data`. **Declare any other permission yourself**, as in
  `expose(..., permissions=[Permission.NETWORK])`: the platform cannot tell that a function writes files
  or calls out.
- **No replacing.** A tool with a name the server already serves stops the server at start-up. Give an
  app `prefix=True` to start its tools' names with its own.
- **Your data.** Give `serve_apps` your own `DatasetStore`, which needs only `get` and `ids`. The analytics
  tools and `list_datasets` then read it by reference, and no connector is needed.
- **Test it.** In your CI, call `check_app(risk, {"desk_var": {...}}, datasets=store)` from
  `quant_agent_os.testing`. With `lock=Path("tools.lock.json")` it also fails when a tool's schemas
  change without a big enough version bump; create the lock with `update_lock=True`.
- **Test the server too.** `build_server_app` takes `serve_apps`'s arguments, plus `keys`, `runs`, and
  `sign_in` (each the configured file by default), and gives the HTTP app `serve_apps` would serve, without
  running it: the same catalog, keys or sign-in, access file, and run store. Drive it in process:

  ```python
  from starlette.testclient import TestClient

  from quant_agent_os.runs import MemoryRunStore
  from quant_platform import build_server_app

  with (
      build_server_app([risk], datasets=store, keys=keys_file, runs=MemoryRunStore()) as app,
      TestClient(app, base_url="http://127.0.0.1:8000") as client,
  ):
      answer = client.post(
          "/v1/tools/desk_var/call",
          headers={"Authorization": f"Bearer {key}"},
          json={"arguments": {"returns": {"dataset": "desks", "column": "growth"}}},
      )
  ```

  A run store named by a path, or the configured one, is closed when the block ends; a store you pass,
  such as `MemoryRunStore`, is yours to inspect and close. Anything your server opens for as long as it
  serves, such as the research corpus, open around the block as well.
- **Deploy it.** Build an image from the platform's, as the example's `Dockerfile` does, then run the
  compose file with it: `QAOS_IMAGE=firm-qaos:1 docker compose -f deploy/compose.yaml up -d server`.
  Mount anything your tools read in a compose override. Every other step above stays the same.

**Internal services.** A read-only service your firm already runs, such as a limits or pricing API,
becomes a tool from a typed stub whose body never runs:

```python
def desk_limits(desk: str) -> Limits:
    """A desk's risk limits, from the limits service."""


risk.call_service(
    desk_limits, url="https://limits.internal/desks/{desk}", method="GET", token_env="LIMITS_TOKEN"
)
```

- The stub's hints and docstring give the schemas, as for `expose`. With POST, the arguments go as a JSON
  object, and data references are read and sent as values, with missing values as null. With GET, they go
  in the query string, and a `{name}` in the path takes that argument. A path argument must be one segment:
  empty, `.`, `..`, and values with `/` or `\` are refused, so a caller cannot reach another endpoint.
- The answer must be JSON that matches the stub's return type. Results carry `retrieved` evidence naming
  the service, and the data's fingerprints.
- The host is fixed in your code. Redirects are not followed, and an answer over the size limit is
  refused. `token_env` names a variable whose value is sent as a bearer token, only over https; it is
  never shown or recorded.
- The tool needs `network`. Grant it with `QUANT_AGENT_OS_GRANTED_PERMISSIONS`. Each call then needs an
  approval, unless you give this tool a standing approval for `network`:
  `QUANT_AGENT_OS_PREAPPROVED_TOOLS='{"network": ["desk_limits"]}'` names exactly the tools that run
  without one ([configuration](configuration.md#standing-approvals),
  [decision 0030](decisions/0030-caller-assertion-and-per-tool-approvals.md)). The older
  `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS='["network"]'` approves `network` for every tool the server
  serves, and draws a warning at start-up. Register only services that change nothing, or declare
  `external_action` too.
- A failure such as an unreachable service or an error status reaches the caller as an internal error
  with the call id; the reason is in the run record.

**Telling the service who called.** A service that scopes its answer to the caller, such as a PM's own
desk's VaR, can receive a signed statement of who is calling. It is off by default; turn it on per tool:

```python
risk.call_service(
    desk_var,
    url="https://risk.internal/desk_var",
    forward_caller=True,
    caller_secret_env="RISK_CALLER_SECRET",  # a variable's name; set it on the server and the service
)
```

- **What is sent.** Each call carries an `X-QAOS-Caller` header: `v1.<payload>.<signature>`. The payload
  is base64url, without padding, of compact JSON with sorted keys: `sub` (the caller's principal), `ten`
  (tenant), `grp` (groups, sorted), `tool` (the tool's name), `iat` and `exp` (seconds since the epoch, 60
  apart), and `nonce` (32 hex digits). The signature is base64url, without padding, of HMAC-SHA256 over
  the ASCII `v1.<payload>`, keyed with the secret's UTF-8 bytes.
- **The secret** is read from `caller_secret_env` at each call, must be at least 32 bytes, and is never
  shown or recorded; nor is the header. Make one with
  `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"` and give each service its own.
- **Refusals.** Registration refuses `forward_caller` without `caller_secret_env` (and the reverse), a
  `caller_secret_env` that is not a variable's name or equals `token_env`, and a plain `http` url off
  loopback. A call with no authenticated caller, as over stdio or in a test that names none, is refused
  before anything is sent, never sent anonymously; so is a call whose secret is unset or too short.
- **In the service**, check the header before answering, and pass the tool's name, so an assertion for one
  endpoint is refused at another. With the platform's wheel installed:

  ```python
  from quant_agent_os.registration import CallerAssertionError, verify_caller

  try:
      caller = verify_caller(headers.get("X-QAOS-Caller"), secret, tool="desk_var")
  except CallerAssertionError:
      ...  # answer 401
  # caller.principal, caller.tenant, caller.groups, caller.nonce
  ```

  It checks the version, the signature in constant time, the payload, the expiry with 10 s of leeway for
  clocks, a lifetime of at most 300 s, and the tool, and raises a subclass naming which failed. Without the
  wheel, copy this standard-library version, which `tests/unit/test_caller_assertion.py` checks against
  the platform's:

  ```python
  import base64, hashlib, hmac, json, time


  def verify_caller(header: str, secret: bytes, tool: str, leeway: int = 10) -> dict:
      """Return the claims of an X-QAOS-Caller header once checked; raise ValueError if not."""
      if len(secret) < 32:
          raise ValueError("the secret must be at least 32 bytes")
      version, payload, signature = header.split(".")  # ValueError unless three parts
      if version != "v1":
          raise ValueError("unsupported version")
      signed = f"{version}.{payload}".encode("ascii")
      digest = hmac.new(secret, signed, hashlib.sha256).digest()
      expected = base64.urlsafe_b64encode(digest).rstrip(b"=")
      if not hmac.compare_digest(expected, signature.encode("ascii")):
          raise ValueError("bad signature")
      claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
      now = time.time()
      if claims["exp"] - claims["iat"] > 300:
          raise ValueError("lives too long")
      if not claims["iat"] - leeway <= now < claims["exp"] + leeway:
          raise ValueError("expired, or issued in the future")
      if claims["tool"] != tool:
          raise ValueError("issued for another tool")
      return claims  # sub, ten, grp, tool, iat, exp, nonce
  ```

- **Replays.** An assertion is good for about a minute. A service that must refuse a repeated request keeps
  the nonces it has seen until they expire. Serve over https so nothing between the two can read it.
- **Test it.** `check_app(app, examples, caller=Caller("pm.equity", groups=frozenset({"equity"})))` calls
  each tool as that caller, and `sign_caller` signs an assertion for the service's own tests.
- **What it proves.** The service learns who the platform says is calling, as the platform verified them:
  it trusts the platform's sign-in, keys, and access file, and anyone who holds the secret can sign. It is
  a statement for scoping answers, not a login.

**Existing code.** For code that is not typed, documented, and pure yet, `qaos legacy` drafts the facades, the
server file, a service for stateful code, and a `check_app` test from a static scan, on your machine; a developer
finishes the draft ([legacy code as tools](products/quant-app-factory.md#legacy-code-as-tools)).

Ready-made tools join an app with `include`, such as a generated application's `agent-tools`, and a tool
written by hand with `@tool` still works through `compose(extra_tools=...)` and `serve_http`.

**Known limits.** A long calculation runs in a thread with a timeout, not in a process that can be
killed, and a result over the size limit is refused, not streamed. Only Python functions and internal
services are supported; code in other languages, such as R or Julia, is not.

The tool names are in your code and not in a setting, so what the server runs is what your repository
says (decisions [0020](decisions/0020-a-firms-own-server.md) and
[0024](decisions/0024-registering-a-firms-functions.md)).

### Read your firm's database as datasets

If your data is in a database, declare each dataset as SQL and let `SqlDatasetStore` serve it
([decision 0032](decisions/0032-sql-dataset-store.md)). Install the `sql` extra, and your database's own
driver in your image; the platform names the driver and does not ship it.

```python
from quant_platform import SqlDatasetStore, serve_apps
from quant_toolbox.data import SqlDataset, sql

oms = sql.connect("postgresql+psycopg://reader@oms.example.com/oms", credential=rds_iam_token)
store = SqlDatasetStore(
    {"oms": oms},
    [
        SqlDataset(
            "returns-growth",
            "SELECT day, desk, ret FROM returns WHERE desk = :desk",
            params={"desk": "growth"},
            index_name="date",
        ),
    ],
    stamps={"oms": "SELECT max(loaded_at) FROM load_log"},  # when to read again
)
serve_apps([risk], datasets=store)
```

| Database | SQLAlchemy URL | Driver to install |
| --- | --- | --- |
| PostgreSQL, RDS | `postgresql+psycopg://user@host:5432/db` | `psycopg[binary]` |
| SQL Server, Azure SQL | `mssql+pyodbc://user@host/db?driver=ODBC+Driver+18+for+SQL+Server` | `pyodbc` |
| Snowflake | `snowflake://user@account/database/schema?warehouse=WH&role=READER` | `snowflake-sqlalchemy` |
| Databricks | `databricks://token@host?http_path=...&catalog=main&schema=serving` | `databricks-sql-connector` |
| DuckDB (a file, or Parquet through `read_parquet(..., hive_partitioning=true)`) | `duckdb:///lake.duckdb` | `duckdb-engine` |

- **SQL is written once.** Binds are always `:name`, whatever the driver's own style, and a list binds as an
  `IN` list. A `Decimal` reads as a float, a date as ISO text, a NULL in a column of numbers as a missing value
  (NaN), and column names as lower case, so Snowflake's upper case reads as everyone else's.
- **Long rows pivot wide, and duplicates are refused.** A query returning `(date, name, value)` becomes a table
  of dates by names. Two rows for one date and name are an error naming them, not a silent sum: say
  `duplicates="sum"` or `"last"` when that is what the data means.
- **Credentials.** Pass `credential=` a function returning a password or token. It is called for every new
  connection, so a 15-minute RDS token or an Entra token never goes stale. The URL holds no secret. A driver
  that takes a connection string (pyodbc) takes its token through `connect_args`.
- **Read-only is best effort.** SQLite, PostgreSQL (psycopg), and a DuckDB file are opened read-only; any other
  dialect warns, because nothing in the library limits it. Give the login only read grants.
- **Freshness.** A dataset is cached until its source's stamp changes: a SQL string returning a version, a
  time to live in seconds (default 60), or a function returning any value. Cached datasets are the same
  objects every caller reads, under per-caller access like any other store.
- **The rare derived dataset** is a Python function in `derived=`, given a `fetch(source, sql, params)`; it
  is where fund-specific logic stays visible.

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
  so `serve_stdio` refuses a catalog with an access policy.

See [decision 0021](decisions/0021-per-caller-access.md).

## 10. Sign in through your identity provider

Off by default. With it on, people add the server as a connector and sign in through your identity provider
(Entra ID, Amazon Cognito, Okta, Keycloak, and so on) instead of pasting a key, and their directory groups decide
what they reach. Keys keep working beside it. Your provider issues the tokens; the server only checks them, in
one of two ways per provider ([decision 0028](decisions/0028-oauth-sign-in-on-a-firms-server.md),
[0031](decisions/0031-jwks-sign-in.md)):

| `verify` | The server | For |
| --- | --- | --- |
| `"introspection"` (default) | asks the provider about each token, by RFC 7662 | Okta, Keycloak, and any provider with an introspection endpoint |
| `"jwks"` | checks the token's signature against the issuer's published keys | **Entra ID and Amazon Cognito**, which have no introspection endpoint |

Both need the provider to put your groups in the token, and both refuse a token unless every check passes.

### With introspection (Okta, Keycloak)

**In your provider:**
- Register a confidential client the server uses to introspect tokens, and note its id and secret.
- Make access tokens carry an audience equal to the server's public MCP URL, such as
  `https://qaos.example.com/mcp`, the scopes `tools:list` and `tools:call` (add `runs:read` for auditors), and a
  claim listing the user's groups.
- Optionally add a `qaos_tenant` claim naming your firm. If its value is not `local`, set
  `tenant_claim_value` to it below; without the claim, set `tenant_claim = ""`, and the issuer and the audience
  then bind the token to this server.

**On the server,** write a sign-in file, and keep the secret in the environment or the home's `.env`:

```toml
# sign-in.toml
resource = "https://qaos.example.com/mcp"

[[authorization_servers]]
issuer = "https://login.example.com"
introspection_url = "https://login.example.com/oauth2/introspect"
client_id = "qaos"
client_secret_env = "QAOS_INTROSPECTION_SECRET"
tenant_claim = ""          # the provider sends no tenant claim
groups_claim = "groups"    # the claim that lists the user's groups
```

```bash
export QAOS_INTROSPECTION_SECRET=...        # or put it in the home's .env
qaos doctor                                  # checks the file and the secret, contacts nothing
qaos serve --http --access access.toml --sign-in sign-in.toml   # or set QUANT_AGENT_OS_SIGN_IN_FILE
```

A firm's own server with `serve_apps` reads the same settings; the [pilot runbook](pilot-runbook.md#6-sign-in-and-entitlements)
shows one behind a mock gateway. In Docker, mount the file read-only in a compose
override, set `QUANT_AGENT_OS_SIGN_IN_FILE`, and pass the secret through the service's environment.

### With signed JWTs (Entra ID, Amazon Cognito)

No client secret is needed: the server reads the issuer's public keys, from `jwks_uri` or from the issuer's
`/.well-known/openid-configuration`. Say which tokens are yours (`audiences`, or `client_ids`) and how to read
their claims.

**Entra ID.** Register an app for the API (its application ID URI, such as `api://qaos`, is the audience), expose
the scopes `tools.list` and `tools.call`, and add app roles for machines. In the app's manifest, set
`accessTokenAcceptedVersion` to 2 (v2.0 tokens) and `groupMembershipClaims` to `SecurityGroup` (groups as object IDs).

```toml
resource = "https://qaos.example.com/mcp"

[[authorization_servers]]
issuer = "https://login.microsoftonline.com/<directory-tenant-guid>/v2.0"
verify = "jwks"
audiences = ["api://qaos"]           # the token's `aud`: an application ID URI or a client id
tenant_claim = ""                    # Entra sends no qaos_tenant claim; the issuer pins the directory
principal_claim = "preferred_username"   # audit shows people, not GUIDs (default: sub)
scope_claims = ["scp", "roles"]      # delegated scopes, and app roles of machine tokens
groups_claims = ["groups", "roles"]  # group object ids, and app roles
provider_tenant_claim = "tid"        # records the directory in the principal: name@<tid>

[authorization_servers.scope_map]    # Entra's dotted names to the server's scopes
"tools.list" = "tools:list"
"tools.call" = "tools:call"
"Tools.Call" = "tools:call"          # an app role that entitles a machine to call tools

[authorization_servers.group_aliases]   # readable names for access.toml
"6f1c2a10-0000-4000-8000-0000000e0001" = "research-eq"
"6f1c2a10-0000-4000-8000-0000000e0002" = "research-macro"
```

**Amazon Cognito.** Use a resource server with the scopes `tools.list` and `tools.call`, and groups for entitlement.
Cognito's access tokens carry a `client_id` and no `aud`, so list your app clients, and require `token_use =
"access"` so an ID token is refused.

```toml
resource = "https://qaos.example.com/mcp"

[[authorization_servers]]
issuer = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_AbCdEf"
verify = "jwks"
client_ids = ["3n4b5v6c7x8z"]        # accepted for tokens with no `aud`
token_use = "access"
tenant_claim = ""
groups_claims = ["cognito:groups"]

[authorization_servers.scope_map]
"qaos/tools.list" = "tools:list"
"qaos/tools.call" = "tools:call"
```

What each setting does, with its default (a setting that the other mode would ignore is refused):

| Setting | Default | Meaning |
| --- | --- | --- |
| `verify` | `"introspection"` | `"jwks"` checks signatures instead of asking the provider. |
| `jwks_uri` | discovered | The keys' address; else the `jwks_uri` in `<issuer>/.well-known/openid-configuration`, whose `issuer` must match. |
| `audiences` | none | The token's `aud` must be one of these (a string such as `api://qaos` or a GUID). Not the `resource` URL. |
| `client_ids` | none | For a token with no `aud`, its `client_id` must be one of these. One of the two lists is required. |
| `algorithms` | `["RS256"]` | Signature algorithms accepted: RS, PS, and ES families only. Never `none`, never HS*. |
| `leeway_seconds` | `30` | Clock skew allowed on `exp`, `nbf`, and `iat` (0 to 300). |
| `token_use` | none | When set, the token's `token_use` must equal it (Cognito: `"access"`). |
| `principal_claim` | `"sub"` | The claim recorded as the caller on every run; `sub` when it is absent. |
| `scope_claims` | `["scope"]` | Claims that grant scopes, each a space-separated string or a list. `["scp"]` for Entra. |
| `scope_map` | none | Renames a provider's scope or role to `tools:list`, `tools:call`, or `runs:read`. Others pass unchanged; `runs:approve` is never granted. |
| `groups_claims` | `["groups"]` | Claims whose values are the caller's groups, joined. Use this, not `groups_claim`. |
| `group_aliases` | none | Renames a group, such as a GUID, to the name `access.toml` uses. |
| `provider_tenant_claim` | none | A claim that must be present, such as `tid`; its value is added to the principal as `name@value`. |
| `tenant_claim`, `tenant_claim_value` | as above | As with introspection. Set `tenant_claim = ""`; or `"tid"` with `tenant_claim_value` to require one directory. |

Machine tokens (Entra client credentials) carry `roles`, no `scp`, no groups. Put `"roles"` in `scope_claims` and
`groups_claims`, map the app role to a scope, and give the same role a group rule in `access.toml`. Entra leaves out
the groups of a user who is in too many (about 200) and points to Microsoft Graph instead; the server makes no
Graph call, reads that user as having no groups, and logs a warning, so access fails closed. Assign app roles, or
limit the groups the token carries, in Entra.

Checking tokens needs nothing but a request for the keys: they are kept for an hour and fetched again for a key
id the server does not know, at most once every 30 seconds per provider. When the keys cannot be fetched, tokens
are refused, and the server tries again after 30 seconds.

### What applies to both

```bash
qaos doctor                                  # checks the file and secrets, contacts nothing
qaos serve --http --access access.toml --sign-in sign-in.toml   # or set QUANT_AGENT_OS_SIGN_IN_FILE
```

- **Tokens are refused unless certain.** A token must be unexpired, for this server (its audience, or with
  introspection the `resource` URL), from a listed issuer, and name the configured tenant, or come from a provider
  set to send none. With introspection it must also be active. A groups claim that is not a list of strings
  refuses the token. The server logs why it refused, never the token.
- **Groups decide access.** The groups are what `access.toml` reads, as a key's groups are. Without an access
  file every signed-in user reaches everything, and the server warns at start-up.
- **The principal is the token's subject** (`sub`) unless `principal_claim` says otherwise, recorded on every run
  as a key's principal is.
- **Scopes map one to one,** after `scope_map`. A token never carries `runs:approve`: approving needs an approver
  key.
- **Clients find the provider** from the RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`, which
  every 401 names. Some providers, Entra among them, do not publish OAuth authorization-server metadata, so check that your
  connector can sign in, or set it up with the provider's addresses by hand.
- **No keys are needed.** With sign-in on, the server starts with an empty or missing keys file.
- **Misconfiguration stops the server.** A missing or invalid file, an unknown setting, a setting the chosen mode
  would ignore, an unset secret, plain http to a host other than loopback, or a tenant other than `local` each
  refuse to start, with the fix.
- **The resource URL's host is admitted** by the DNS-rebinding protection without `--allowed-host`.
- **Revocation takes up to a minute with introspection.** Results are cached for 60 seconds, so a revoked token
  can still be accepted for that long. Each provider is asked at most 600 times a minute and 8 at once; keep the
  proxy's limit on unauthenticated requests, which is the first defence. A signed JWT cannot be revoked: it is
  accepted until its own expiry, so ask your provider for short-lived access tokens (an hour or less).

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
  opentelemetry-instrument qaos serve --http
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
key can give ([security](security.md)), unless `QUANT_AGENT_OS_PREAPPROVED_TOOLS` gives the named tools a
standing approval ([configuration](configuration.md#standing-approvals)).

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
Run it before you trust an upgrade, on either variant, as with the smoke test. CI runs it on both for each
release tag, and for pull requests that change the deploy files or the stores.

`deploy/firm-image-test.sh` is the same check for [your own tools](#8-add-your-own-tools), and CI runs
it too. It builds the example's image on the platform's, runs it with the compose file's restrictions,
and checks that the firm's tool is listed, called, and recorded under the key's principal.
