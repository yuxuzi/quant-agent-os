# 0015: Metering, quotas, plan entitlements, and tool compatibility

- Status: accepted (approved by the user 2026-11-03, with Q-14 to Q-18 as proposed)
- Date: 2026-11-03
- Plan: [agent-surface P7](../plans/agent-surface.md), backlog B-36. Builds on
  [0011](0011-rest-api-and-api-keys.md) (API keys), [0012](0012-remote-mcp-over-http.md)
  (remote MCP), [0013](0013-identity-and-tenancy.md) (tenants), and
  [0014](0014-approvals-in-the-client.md) (approvals).

## Context

After P4 to P6, one server can serve many tenants over HTTP MCP and REST. Every tenant gets
the same unlimited service, though, and nothing records what each one used:
- **No usage.** The run store records every call (`ToolCallRecord`: tool, status, duration),
  but not result sizes. It has no per-tenant view and no export, and usage can only be read
  run by run.
- **No limits.** A tenant's toolsets are set in the tenants file, but there is no rate limit
  and no monthly quota. One client in a loop can use a shared server's capacity, its model
  budget downstream, and its knowledge store. The approval page has no attempt limit either
  (decision 0014, consequences).
- **No compatibility promise.** Every tool has `version = "1"`. A skill or a client prompt
  written against today's schemas has no way to notice a breaking change, and nothing stops
  a schema change that leaves the version as it is.
- **No operations signals.** `opentelemetry-api` is locked only as FastAPI's transitive
  dependency (decision 0011). Nothing emits spans or metrics, and the remote surface has had no
  security review.

These are the seams a hosted service needs, but self-hosted teams need them too: a shared team
server needs quotas just as much as a hosted one. This decision covers only the mechanisms.
Prices, the billing provider, and the open-core line (Q-1) are not part of it.

## Decision

### 1. Metering: a usage event per call, in the run store (P7.1)

1. **One usage event per executed call.** It holds the tenant, the principal, the interface
   (`mcp`, `rest`, `sdk`), the tool and its version, the toolset, the status, `duration_ms`,
   the input and output sizes in bytes, the run and call ids, and the time. It never holds
   arguments, outputs, or passages, only their sizes.
2. **Written with the call.** `RunRecorder.record_call` hands the store the `ToolCallRecord`
   and its usage event together, and the store writes both in one transaction (run store
   schema 5, a `usage_events` table). A recorded call therefore always has its usage event, and
   an unrecorded call never does. Usage events have their own table because they are read by
   tenant and time, not by run, and because a later retention policy for runs must not delete
   usage. Recording stays best-effort as today: a failed store write is logged and the call's
   result is still returned, so usage can undercount but never blocks a call.
3. **What counts (Q-14).** Every call is recorded. Calls with status `ok`, `error`, or
   `timeout` are billable, because they used the server. `invalid_input`, `denied`,
   `approval_required`, `unknown_tool`, and quota refusals are recorded but are not billable.
   The approved retry of a waiting call counts once, when it runs.
4. **Export is an operator command.** `quant-platform usage export --tenant acme --from
   2026-11-01 --to 2026-12-01 [--by day|tool] [--format csv|jsonl]` reads the run store. It is
   not an agent tool or an MCP route (like ingestion: writes and cross-tenant reads stay
   operator commands). A tenant reading its own usage over REST is later work.
5. **Without a run store there is no metering.** `serve-mcp --no-record` writes nothing, as
   today. Recording is on by default, so local stdio use is metered into the local run file,
   which only its owner reads.

### 2. Plans: entitlements, quotas, and rate limits per tenant (P7.2)

1. **Plans live in the tenants file.** A plan names an upper bound; a tenant names its plan.

   ```toml
   [plans.team]
   toolsets = ["research", "analytics"]
   calls_per_minute = 120        # rate limit per tenant
   monthly_calls = 50_000        # billable calls per calendar month (UTC)
   max_output_bytes = 200_000    # caps the executor's per-call output limit

   [tenants.acme]
   plan = "team"
   toolsets = ["research"]       # optional: narrows the plan, never widens it
   ```

   - A tenant without a plan has no limits, so existing tenants files and single-team servers
     behave as before. An unknown plan name is a tenants-file error at start-up.
   - A tenant's `toolsets` must be a subset of its plan's. A tenants file that widens a plan is
     rejected at start-up with a message that names the toolset.
2. **Out-of-plan means unlisted.** The tenant's catalog is composed only from entitled
   toolsets, so out-of-plan tools, resources, and prompts are not listed. Calling one by name
   gets the existing `unknown_tool` result. Entitlement is part of composition, not a check
   on each call, so a tool can never be listed and refused at the same time.
3. **Knowledge packs (later).** A plan will also name the knowledge packs a tenant may search.
   No packs exist yet, so this decision reserves the `packs` key and rejects it until they do.
4. **Checked before the call, never during it.** A `UsageGate` in core (next to the approval
   gate) is consulted before the executor starts a call. Once a call starts it runs to the end
   and is metered, even if it takes the tenant over its quota. MCP and REST share the gate
   through `Surface`, so business logic stays out of MCP (decision 0002).
5. **A clear, structured refusal.** A refused call gets a new status, `quota_exceeded`, with
   `retryable` and a hint naming the limit and when it resets, for example "Tenant acme used
   its 50,000 calls for November; the quota resets 2026-12-01T00:00Z." REST answers 429 with
   `Retry-After`; MCP returns the structured error like every other failure (P1.3).
6. **Where the counts come from (Q-15).**
   - **Monthly quota.** Counted from `usage_events` in the run store, which every replica
     shares on PostgreSQL. Concurrent calls can overshoot by at most the concurrency in
     flight; that is accepted and documented.
   - **Rate limit.** A token bucket in each process, so with N replicas the effective limit
     is up to N times the plan's. This needs no shared state on the hot path. A shared limiter
     (PostgreSQL or Redis) is later work, when a deployment needs exact limits.
7. **The approval page gets an attempt limit.** Five failed submissions for one request deny
   it. Submissions per client address are limited to 30 a minute. This closes decision 0014's
   open consequence.

### 3. Tool compatibility (P7.3)

1. **Tool versions follow semver.** `version` becomes `MAJOR.MINOR.PATCH` (today's `"1"`
   becomes `"1.0.0"`).
   - **Major:** a breaking change to the input schema (a new required field, a removed or
     renamed field, a narrowed type), to the output schema, or to the meaning of a result.
   - **Minor:** additions only (an optional input, a new output field, a new enum value in
     the output).
   - **Patch:** fixes that change neither schema.
2. **CI enforces it.** A checked-in `tool-versions.lock` records each tool's version and a hash
   of its schemas. The contract tests fail when a schema changes without the lock being
   updated, or when a breaking change comes without a major bump. The test classifies the
   change by diffing the JSON Schemas.
3. **Deprecation window (Q-16).** A breaking change ships as a new tool name (for example
   `search_passages_v2`) next to the old one, because one name has one schema on a server.
   The old tool is marked deprecated in its description and `_meta`, naming its replacement
   and a removal date at least 90 days, and one minor platform release, later. Removal is a
   major platform release.
4. **A changelog for tools.** `docs/tools-changelog.md` lists every tool version change, with
   deprecations and removals. `CHANGELOG.md` links to it.
5. **The server advertises its version.** MCP `serverInfo.version` is the platform version.
   A resource, `qaos://server/info`, lists the platform version, each tool's version, any
   deprecations, and the registry digest. Each skill states the platform major version it was
   written for, and its instructions tell the client to read `qaos://server/info` once and
   tell the user when the major differs.

### 4. Operations (P7.4)

1. **OpenTelemetry, API only.** The executor emits a span per call and two metrics,
   `qaos.tool.calls` (a counter) and `qaos.tool.duration` (a histogram), with `tenant`, `tool`,
   and `status` attributes, through `opentelemetry-api`. It adds no SDK or exporter: an
   operator who wants telemetry installs one, and without one the API does nothing.
   `opentelemetry-api` becomes a direct dependency of the `mcp` and `api` extras (Q-17); it is
   already in the lock. Span attributes never carry arguments, outputs, principals, or keys.
2. **SLOs.** `docs/deployment.md` defines two service-level indicators: availability (calls
   that did not end in `error` or `timeout`, per tenant and tool) and p95 latency per tool.
   It gives an example dashboard definition built on the metrics above.
3. **Security review before any public endpoint.** A written review of the remote surface
   (HTTP MCP, REST, OAuth, the approval page) against the OWASP API Security Top 10 and the
   MCP security best practices (token passthrough, confused deputy, DNS rebinding, session
   handling). It goes in `docs/security-review.md` and is a release gate of B-22.

### Out of scope

A billing provider or invoices, prices, the licence of hosted parts (Q-1), MCP registry
listings (Q-4), and a self-service admin API for plans.

## Alternatives considered

- **Derive usage from `tool_calls`.** No new table, but calls carry no sizes, are read by run,
  and go when runs are pruned. Usage has to outlive runs.
- **Meter in the MCP adapter or HTTP middleware.** Easy to add, but REST and the SDK would
  bypass it, and it would put business logic in MCP (decision 0002). The executor and
  recorder see every interface.
- **Hide nothing and refuse out-of-plan calls.** Simpler composition, but the model would
  spend context on tools it cannot use, and plan P1 showed that definition bytes matter.
- **Refuse a tool by plan per call.** Cheap to build, but tools could be listed and then
  refused. Composition already decides what a tenant sees.
- **A shared rate limiter now.** Exact limits across replicas, but it needs Redis (a new
  dependency) or a PostgreSQL write on every call. Per-process buckets suffice until a
  deployment shows otherwise.
- **Serve several majors of one tool under one name.** MCP lists one schema per name, so the
  client could not choose. A new name is explicit and visible in the changelog.

## Consequences

- Operators can see and export usage per tenant, and a shared server can protect itself from
  one tenant.
- **Schema 5.** The run store gains `usage_events`. Like schema 4, older binaries refuse a newer
  file.
- **A new call status.** `quota_exceeded` joins `CallStatus`, so clients that match on status
  see a new value. This is additive, but the contract and docs must list it.
- **Tool versions all change once.** `"1"` becomes `"1.0.0"` for every tool, which changes
  the registry digest. After that, the lock file makes every schema change a deliberate,
  reviewed step.
- **Per-replica rate limits are approximate.** Documented, with the shared limiter as the fix
  when needed.
- **No new packages in the lock.** `opentelemetry-api` is already locked through FastAPI; Q-17
  only declares it directly, which the boundary rules say needs approval.

## Resolved questions

- **Q-14 (as proposed).** `ok`, `error`, and `timeout` count; refusals and invalid input do not.
- **Q-15 (as proposed).** A token bucket per process now, and a shared limiter only when a
  deployment needs it. Monthly quotas are counted from the shared run store.
- **Q-16 (as proposed).** A breaking change ships as a new tool name; the old one stays,
  deprecated, for at least 90 days and one minor release.
- **Q-17 (as proposed).** `opentelemetry-api` (already locked) is a direct dependency of the
  `mcp` and `api` extras, not of core.
- **Q-18 (as proposed).** Metering, plans, quotas, and the compatibility policy are open source;
  only billing integration and the hosted operations would be paid.

## Implementation notes (2026-11-03, P7.2)

- **Where it lives.**
  - `quant_agent_os.runtime.quotas`: `UsageGate`, `Refusal`, `TokenBucket`, and `PlanLimits`.
  - `ToolExecutor(..., usage=, max_output_bytes=)` asks the gate after the tool, permissions,
    and input are checked, before any approval is used.
  - `quant_platform.tenants`: `Plan`, `TenantsFile.plans`, `TenantConfig.plan`,
    `entitled_toolsets`, and `plan_limits`, which `TenantCatalogs` applies to each planned
    tenant's executor.
- **The monthly count.** `RunStore.count_billable` counts a tenant's billable usage events in
  the UTC month. `PlanLimits` re-reads it every 30 seconds and counts admitted calls locally in
  between. The run store is one SQLite file per server today, so this decision's "every replica
  shares" holds only once a shared run store exists; until then the count is exact for one
  server process, up to the calls in flight.
- **Approvals and quotas.** A quota refusal does not supersede a call that waits for an
  approval: the run stays `awaiting_approval`, and an approved request whose retry is refused
  returns to `approved`, so the next retry runs it without a new approval.
- **The approval page.** Failed attempts (a wrong form token, a key that cannot decide the
  request, or the requester's own key) are counted per request, in the process. The form
  token is now checked after the request is found, so failures count against it; request ids
  have 128 random bits, so this reveals nothing the `GET` page does not.

## Implementation notes (2026-11-03, P7.3)

- **Where it lives.** `parse_version` and `Deprecation` in `quant_agent_os.tools.contracts`;
  `classify`, `bump`, and `check_lock` in `quant_agent_os.tools.compatibility`; `server_info_tool`
  and `server_info_resource` in `quant_agent_os.tools.server_info`. The platform's `compose`
  adds `server_info` to every catalog, toolset `platform`, so a tenant's `server_info` lists only
  that tenant's tools.
- **The lock.** `tool-versions.lock` holds full schemas, not hashes, so the test can say what
  kind of change was made. `apps/quant-platform/tests/tool_lock.py --update` refuses to write
  while a bump is smaller than the change. The classifier is conservative: any structural
  change it does not recognise is major.
- **One platform version.** Every workspace package is released at one version, which a test
  enforces, so MCP `serverInfo.version` (the core version) is the platform version.
- **Fixed resources.** A resource without variables (`qaos://server/info`) is listed by MCP
  `resources/list` rather than as a template.
