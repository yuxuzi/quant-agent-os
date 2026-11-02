# 0012: Remote MCP over Streamable HTTP, authenticated like REST

- Status: proposed (needs the user's approval before implementation, per AGENTS.md)
- Date: 2026-11-02
- Plan: [agent-surface P4](../plans/agent-surface.md), backlog B-33

## Context

MCP is served over stdio only (decision 0002), so a team or hosted deployment cannot be added
to Claude, Cursor, or Copilot as a remote MCP server. Today the Docker team server speaks REST
(decision 0011), which those clients do not use. A hosted product needs MCP over HTTP.

An HTTP port can be reached by anyone on the network, so the transport must authenticate every
caller and must not widen what the executor allows. The installed MCP SDK (1.30) already
provides the parts needed: a Streamable HTTP session manager with a stateless mode, a
bearer-token verifier interface, DNS-rebinding protection, and the URL-elicitation-required
error. No new dependency is needed.

## Decision

- **One HTTP app, two interfaces.** `quant-platform serve-http` serves the composed catalog
  (decision 0003's composition root, B-31) as MCP at `/mcp` and REST at `/v1`, on one port and
  in one process. Both use the same executor, recorder, policy, and API keys.
  `quant-platform serve-mcp` (stdio) is unchanged.
- **Stateless Streamable HTTP.** Each request stands alone, with no server-side session, so
  replicas need no sticky routing and a restart drops nothing. Responses are JSON; there are no
  SSE streams in this step. P6 delivers in-client approvals with the URL-elicitation-required
  error (−32042), which works without a session, so it does not force sessions back.
- **Authentication reuses decision 0011's keys and scopes.** A bearer `qaos_…` key goes
  through the SDK's `TokenVerifier` interface. A missing or unknown key gets 401 with
  `WWW-Authenticate`, and a missing scope gets 403. No new scope:
  - `tools/list`, `resources/templates/list`, and `prompts/list`/`get` need `tools:list`.
  - `tools/call` and `resources/read` (which calls a tool) need `tools:call`.

  The verifier interface is where P5's OAuth 2.1 tokens plug in. Keys stay for machine
  clients.
- **Scopes narrow; they never widen.** The server's `PermissionPolicy` comes from the same
  settings as stdio, so it is read-only by default. A consequential call still returns
  `approval_required`, and no HTTP route, key, or client can approve it (decision 0005).
- **The audit trail names the caller.** Calls are recorded with actor `mcp-http` and the key's
  principal, as REST calls are (actor `rest`).
- **Safe defaults, as for REST:**
  - The server refuses to start without at least one enabled key.
  - It binds `127.0.0.1` unless told otherwise, and warns that TLS belongs in a reverse proxy
    on any other address.
  - DNS-rebinding protection is on. `Host` and `Origin` must match the configured allowed
    hosts; the default is loopback, and `--allowed-host` adds the public name.
  - Request bodies are capped at the same 5 MiB.
  - CORS is off, and error bodies and logs never echo the key.
- **The team image serves the platform.** `deploy/Dockerfile` installs `quant-agent-os-platform`
  and runs `serve-http`, so the compose deployment answers MCP and REST. The research-only
  `serve-api` stays for existing deployments for one release.

## Alternatives considered

- **Stateful sessions.** These would allow server-to-client requests mid-call (form
  elicitation, progress over SSE), but they need sticky routing and session storage, and they
  are a session-hijacking surface. They are not needed for P4 to P6, so not now.
- **Separate ports for MCP and REST.** That means two listeners to secure and document, for no
  benefit.
- **OAuth first.** OAuth is what connectors need, but it needs an identity provider and tenancy
  (P5). Keys unblock team servers now, and the verifier seam keeps the later change local.

## Consequences

- Claude Code connects with `claude mcp add --transport http <url> --header "Authorization:
  Bearer …"`. A team gets one URL for every toolset.
- Contract tests will assert that HTTP MCP results equal stdio, REST, and direct calls. They
  will cover 401 and 403, a rejected `Host` or `Origin`, and that no key appears in a log or
  error.
- Rate limiting and quotas are not in this step (P7). Until then, an operator should put a
  rate-limiting proxy in front of any non-loopback deployment, and `deployment.md` will say so.
- The platform package depends on App Factory, so the image grows by pytemplate-uv and its
  dependencies. If that matters, the factory toolset can become an optional extra of the
  platform package: open question Q-5 below.

## Open questions

- **Q-5.** Should the platform's factory toolset be an optional extra, to keep the team image
  small?
- **Q-6.** Should `serve-http` keep the research-only REST routes' paths (`/v1/tools/...`)
  unchanged? Proposed: yes. The tool names are the same, so existing REST clients keep
  working.
