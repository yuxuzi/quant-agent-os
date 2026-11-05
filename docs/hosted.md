# Hosting many customers

Most firms do not need this page: [deployment.md](deployment.md) runs one server for one team, with
API keys. This page is for a vendor serving several customers from one server
([decision 0017](decisions/0017-hosted-behind-a-command-group.md)). Everything here runs through
`qaos hosted`, and the default commands never load it.

Before any public endpoint, do the steps in
[security-review.md](security-review.md#before-a-public-endpoint).

## Many tenants (decision 0013)

One server can serve several teams or customers, each with its own corpus, datasets, toolsets,
and permissions:

```bash
quant-platform tenant add acme --store postgresql://qaos_reader@postgres/qaos \
  --toolsets research,analytics --issuer https://login.acme.example
qaos ingest /corpus/acme --store postgresql://qaos_ingest@postgres/qaos --schema qr_acme
qaos data add returns.csv --id returns --tenant acme
qaos keys new --principal acme-bot --scope tools:list --scope tools:call --tenant acme
qaos hosted serve --tenants ~/.quant-agent-os/tenants.toml --host 0.0.0.0
```

- **The tenants file** (`--tenants`, or `QUANT_AGENT_OS_TENANTS_FILE`) lists each tenant's
  store, schema, datasets directory, toolsets, permissions, and pinned issuer.
  `qaos hosted tenant list` shows it with passwords hidden, and `tenant disable` stops
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
ACME_INTROSPECTION_SECRET=... qaos hosted serve --tenants tenants.toml \
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
qaos keys new --principal lead --scope runs:approve --tenant acme
qaos hosted serve --tenants tenants.toml --approval-url https://qaos.example.com
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
