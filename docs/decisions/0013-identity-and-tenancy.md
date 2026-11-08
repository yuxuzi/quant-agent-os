# 0013: Tenants, and OAuth 2.1 for remote MCP clients

- Status: accepted (approved by the user 2026-11-02, with Q-7 to Q-9 as proposed); amended by
  [0023](0023-no-hosted-service.md), which removes the multi-tenant server
- Date: 2026-11-02
- Plan: [agent-surface P5](../plans/archive/agent-surface.md), backlog B-34. Builds on
  [0011](0011-rest-api-and-api-keys.md) (keys) and [0012](0012-remote-mcp-over-http.md)
  (remote MCP).

## Context

A team server today has one corpus, one dataset directory, one run store, and one policy. A
hosted service needs several customers on one deployment, each seeing only its own data. It
also needs the sign-in Claude and Cursor connectors expect: the MCP authorization spec makes a
remote server an OAuth 2.1 resource server. API keys stay for machines, but a person adding a
connector should sign in through their organisation's identity provider, not paste a key.

What already helps:
- the PostgreSQL knowledge store takes a schema name;
- runs and audit events carry a principal (0011);
- `build_server` names each caller through `authorize` (0012);
- the SDK's bearer backend can check a token's audience.

## Decision

### Tenants

- **A tenant is the unit of isolation.** Each tenant has its own knowledge store, datasets, run
  store and audit trail, permission policy, and toolsets. A tenant id matches
  `^[a-z0-9][a-z0-9-]{0,62}$`.
- **The tenant comes from the credential, never from the request.** No argument, header, path,
  or resource URI names a tenant. A key's tenant is in the keys file, and a token's comes from
  a configured claim. Resource URIs stay tenant-free (`qaos://passage/{chunk_id}`) and resolve
  inside the caller's tenant, so a guessed URI from another tenant finds nothing.
- **Where each tenant's data lives:**
  - **Knowledge store.** A PostgreSQL schema per tenant (`qr_<tenant>`). The server's
    read-only role is granted `SELECT` per schema; ingestion uses a per-tenant owner role.
    With SQLite, it is a file per tenant (`<root>/<tenant>/kb.db`).
  - **Datasets.** A directory per tenant (`<datasets>/<tenant>/`).
  - **Runs.** The run store gains a `tenant` column (schema 4). Every read filters by it, and
    `/v1/runs` shows only the caller's tenant. Existing rows belong to `default`.
- **One tenant is the default.** Without a tenant configuration, everything is tenant
  `default`, so today's single-team deployments behave exactly as before.
- **Per-tenant catalogs.** The HTTP server composes each tenant's catalog (its stores, policy,
  and toolsets from a tenants file) on first use, caches a bounded number, and routes each
  request to its caller's catalog. stdio stays single-tenant.

### Identity

- **A principal is now `(tenant, subject, scopes, kind)`.** `kind` is `key` or `oauth`, and
  runs and audit events record the tenant beside the principal.
- **API keys gain a tenant.** Each entry in the keys file has a `tenant`, `default` if absent.
  `quant-agent-os api-key new --tenant acme`.
- **The server is an OAuth 2.1 resource server.**
  - It publishes Protected Resource Metadata (RFC 9728) at
    `/.well-known/oauth-protected-resource`, naming the configured authorization server, and
    its 401 responses point to it in `WWW-Authenticate`.
  - It accepts a bearer token only if the token's audience is this server (RFC 8707). It never
    passes a client's token on to any other service.
  - It does not issue tokens or run a login page. The tenant's identity provider (Entra ID,
    Okta, Auth0, Keycloak, and so on) does.
- **Token verification: introspection (RFC 7662).** This needs no new dependency, since httpx
  is already locked. Results are cached by the token's SHA-256 for at most 60 seconds, and
  never past the token's expiry. Local JWT verification would avoid that round trip but needs
  a JOSE library (Q-7).
- **Scopes stay the same:** `tools:list`, `tools:call`, `runs:read`. They map from token scopes
  one to one.

### Tenant administration

- **An operator command, not an agent tool.** Ingestion and dataset pushes stay off MCP:
  - `quant-research ingest --tenant acme ...`
  - `quant-agent-os dataset push --tenant acme ...`
  - `quant-agent-os tenant add/list/disable`, which manages the tenants file and, on
    PostgreSQL, creates the schema and its grants.
- **An HTTP admin API is not part of P5** (Q-9). It is a new write surface with a new scope,
  and a hosted onboarding flow needs it later. It gets its own decision.

## Alternatives considered

- **One schema with a `tenant_id` column and row-level security.** That would put tenant
  filters into every query of a complex store: full-text search, pgvector, and graph
  expansion. A missed filter would be a cross-tenant leak. A schema per tenant reuses the
  store unchanged, and gives a per-tenant backup and a clean delete.
- **A server process per tenant.** Simplest to reason about, but costly and slow to onboard at
  many small tenants. A per-tenant catalog inside one process gives the same isolation at the
  data layer, which the isolation tests check.
- **Running our own authorization server.** Customers already have identity providers, and
  issuing tokens is a security product in itself.

## Consequences

- **Isolation tests become a release gate.** Two tenants on one server must never see each
  other's passages, datasets, runs, or resources: through any tool, any resource URI (guessed
  or real), REST `/v1/runs`, or a cached catalog. Keys and tokens from one tenant must fail
  against another's data.
- **Existing deployments keep working unchanged** as tenant `default`. Run stores migrate in
  place to schema 4.
- **Each tenant's identity provider** needs a registered client and a configured audience. A
  `deployment.md` section will cover Keycloak in a test compose file.
- **Per-tenant policy** lets one tenant grant `network` (a hosted embedder) without granting
  it to all.

## Resolved questions

- **Q-7.** Introspection only, with no new dependency. Local JWT verification waits until
  latency needs it.
- **Q-8.** A schema per tenant.
- **Q-9.** The HTTP tenant-admin API is a later decision; P5 ships operator commands.
- **Q-10 (as proposed).** A configurable claim, `qaos_tenant` by default, with the issuer
  pinned per tenant.

## Implementation notes (2026-11-02)

- **The default tenant is `local`.** The run store already recorded that value, so existing
  stores need no migration and no schema bump; an index on (tenant, started_at) was added.
- **Where it lives.** `quant_agent_os.tenancy` and `quant_agent_os.serving` (`Surface`,
  `Tenancy`, `SingleTenant`) are in Core. REST's `build_app` and HTTP MCP's `add_mcp_route`
  take a tenancy, and `build_server`'s `route` hook returns the caller's surface and recorder.
  The platform's `TenantCatalogs` composes each tenant's catalog from the tenants file.
- **OAuth configuration** is in the tenants file. `[[authorization_servers]]` lists issuers,
  their introspection endpoints, and a client id, and names the environment variable that
  holds the secret. With several providers and opaque tokens the server could not know whom to
  ask without leaking the token, so a JWT's unverified `iss` chooses the server and an opaque
  token needs exactly one.
- **Ingestion** uses the one `qaos_ingest` role for every tenant's schema, not a role per
  tenant. The operator runs ingestion, and the server's role stays read-only everywhere.
- **Not built.** REST stays API keys only, since OAuth is for MCP connectors. A Keycloak test
  compose is documented, not shipped, because there is no Docker in this environment to
  verify it. The HTTP admin API is a later decision (Q-9).
