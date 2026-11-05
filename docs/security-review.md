# Security review of the remote surface

- Status: first review, findings open (B-36 P7.4,
  [decision 0015](decisions/0015-metering-quotas-and-entitlements.md); a release gate of B-22)
- Date: 2026-11-03
- Reviewed: branch `feat/b36-otel` at `b03b5a0` (P7.1 to P7.3, plus P7.4 telemetry)
- Method: reading the code and its tests against the
  [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
  and the [MCP security best practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices).
  An AI coding agent did this review. It is not a penetration test, and it does not replace an
  independent review before a public multi-tenant endpoint (see the last section).

The general model is in [security.md](security.md). This page checks it against the code and
lists what is still open.

## The surface

`qaos hosted serve` serves everything below on one port; the default `serve --http` serves all but the approval page and OAuth metadata. Local stdio (`qaos serve`) is
out of scope: it trusts the user who started it.

| Route | Who may call it | Guarded by |
| --- | --- | --- |
| `POST /mcp` (Streamable HTTP, stateless) | API key or OAuth token, scope per method | `mcp/http.py`: bearer middleware, `caller_of`, `tenant_route` |
| `/v1/tools`, `/v1/tools/{name}/call`, `/v1/runs` | API key with the route's scope | `api/app.py`: `requires(scope)`, `surface_of` |
| `GET`/`POST /approve/{id}` | Anyone with the link; deciding needs an approver key | `quant_platform/hosted/approval_page.py`, `approval_desk.py` |
| `/.well-known/oauth-protected-resource/mcp` | Anyone (public metadata) | MCP SDK |
| `/healthz` | Anyone; returns `{"status": "ok"}` only | — |

Every tool call, from any route, then passes the executor's checks in order: registered tool,
permission policy, input validation, plan limits, approval, timeout, and output size.

## OWASP API Security Top 10 (2023)

| Risk | What the code does | Evidence | Verdict |
| --- | --- | --- | --- |
| **API1 Broken object-level authorization** | The tenant comes only from the verified credential. Each tenant has its own store, datasets, and registry; run reads are filtered by tenant; a guessed resource URI resolves inside the caller's tenant. Within a tenant, any `runs:read` key reads every principal's runs (by design: the tenant is the boundary). | `tests/contract/test_mcp_http.py`, `tests/server/test_platform_tenants.py` (isolation release gate) | Pass |
| **API2 Broken authentication** | Keys have 256 random bits, are stored as SHA-256 hashes (file mode 0600), and are compared in constant time against every hash. OAuth tokens are introspected and must be active, unexpired, for this server's audience, from a pinned issuer, and name a pinned tenant. Tokens are never sent to a server that could not have issued them. | `tests/unit/test_api_key_cli.py`, `tests/server/test_hosted_oauth.py` | Pass, with F-1 and F-4 |
| **API3 Broken object-property-level authorization** | Inputs are validated against each tool's model, and REST rejects unknown body keys. Outputs are validated against the output model. Stored arguments and outputs are redacted; usage events and telemetry carry sizes and labels only. | `tests/unit/test_runs.py`, `tests/unit/test_telemetry.py` | Pass |
| **API4 Unrestricted resource consumption** | Bodies are capped at 5 MiB (approval forms at 8 KiB), each tool has a timeout and an output cap, and a tenant's plan can limit calls a minute and a month. Unauthenticated requests are not limited, and a timed-out tool thread keeps running. | `tests/server/test_hosted_limits.py`, `tests/server/test_platform_plans.py` | Partial: F-1, F-2 |
| **API5 Broken function-level authorization** | Scopes are checked per MCP method and per REST route. No route can approve: approving needs `runs:approve`, which no key holding `tools:call` and no OAuth token can carry. Operator actions (ingestion, keys, tenants, usage export) are CLI commands, not routes. | `tests/server/test_hosted_approvals.py` | Pass |
| **API6 Unrestricted access to sensitive business flows** | A consequential call needs a per-call approval bound to the run, tool, and input hash, decided once by a different person (four eyes except `local`). Five failed attempts deny a request; each address may submit 30 forms a minute. Remote servers never write users' files (`create_app` is not served over HTTP). | `tests/server/test_hosted_approvals.py` | Pass, with F-3 |
| **API7 Server-side request forgery** | No tool takes a URL. Outbound calls go only to operator-configured hosts: the introspection URLs in the tenants file and the hosted embedder. | Code reading: `quant_platform/hosted/oauth.py`, `quant_research/infrastructure/remote_embedding.py` | Pass |
| **API8 Security misconfiguration** | Binds loopback by default and warns otherwise; no CORS, docs pages, server header, or date header; DNS-rebinding protection on `/mcp`; the approval page sends a strict CSP, `no-store`, `no-referrer`, and `DENY` framing. A failed tool's exception text is returned to the caller. | `tests/contract/test_mcp_http.py`, `tests/contract/test_rest_api.py` | Pass, with F-5 |
| **API9 Improper inventory management** | Tools are registered explicitly, never discovered. Each has a semver version checked against `tool-versions.lock`; `server_info` and `qaos://server/info` list them; deprecations name a replacement and a removal date. | `tests/server/test_tool_lock.py` | Pass |
| **API10 Unsafe consumption of APIs** | Introspection responses are type-checked claim by claim, and failures refuse the token. Retrieved passages are marked `"trust": "untrusted"`, and no tool output reaches the platform's own planner. | `tests/server/test_hosted_oauth.py`, research contract tests | Pass |

## MCP security best practices

| Practice | Status |
| --- | --- |
| **No token passthrough.** The server accepts only tokens issued for it (audience equals `--resource-url`) and never forwards a client's token to another service. Tools never see credentials. | Met |
| **Confused deputy.** The server is a resource server only. It does not proxy OAuth to a third party or hold a static client id for users, so there is no consent to bypass. | Not applicable |
| **Session hijacking.** The transport is stateless: there are no session ids to steal or inject, and every request is authenticated on its own. | Met |
| **Scope minimization.** Four scopes; OAuth tokens get only those they name, never `runs:approve`. The policy is read-only by default per tenant. | Met |
| **DNS rebinding and local exposure.** `Host` and `Origin` must be loopback or an `--allowed-host`. | Met |
| **Untrusted tool output (prompt injection).** Passages are marked untrusted, server instructions say not to follow them, and consequential calls still need a person's approval. The client's own behaviour is outside the server's control. | Met as far as a server can |

## Findings

Severity is for a public, multi-tenant endpoint. On a loopback or private team server, F-1
and F-2 matter much less.

| Id | Severity | Finding | Recommendation |
| --- | --- | --- | --- |
| **F-1** | Medium | **Unauthenticated amplification through introspection.** A JWT-shaped token whose unverified `iss` names a configured provider triggers one introspection request, and failures are not cached. Anyone can make the server send requests to a tenant's identity provider at the rate they send requests. Plans do not apply before authentication. | Rate-limit unauthenticated requests in the reverse proxy (already required). Then cache refusals for a short time and bound concurrent introspections per provider. **Fixed in 0.1.1:** refusals are cached for 30 s, and each provider gets at most 600 introspections a minute and 8 at once. |
| **F-2** | Medium | **Partly fixed.** A Python thread cannot be killed, so a timed-out tool still keeps its thread until it returns. Each executor now runs at most 16 calls at once, and a timed-out call keeps its slot, so threads can no longer accumulate without limit. When every slot is taken, calls return `busy` (REST: 503 with `Retry-After`). Tools that hang for good reduce capacity until a restart. | Run slow tools in a process that can be killed. |
| **F-3** | Low | **The approval page is a capability URL.** Anyone with the link sees the tool, the redacted arguments, and the requester's principal (not the key). Links can surface in browser history, proxy logs, and auto-instrumented HTTP spans. Deciding still needs an approver key. | Keep the 15-minute expiry. Document that telemetry and proxy logs see these paths (done in deployment.md). Consider requiring an approver key to view as well. |
| **F-4** | Low | **Partly fixed.** API keys can expire, and the server re-reads the keys file without a restart. An OAuth token can still stay accepted for up to 60 seconds after it is revoked (the introspection cache). | Accept the OAuth window, or shorten the cache. |
| **F-5** | Low | **Failure messages echo exception text.** A tool that raises returns `<ExceptionType>: <message>` to the caller, which can include internal details such as file paths. | Return a generic message with the call id, and keep the detail in the run record. **Fixed in 0.1.1:** `ValueError` and `LookupError` messages pass through; everything else is generic, with the text in `error_detail`. |

No finding allows a cross-tenant read, an approval without an approver key, or a call outside
a tenant's permissions.

## Before a public endpoint

1. Put a reverse proxy in front: TLS, a rate limit on unauthenticated requests (F-1), and
   `FORWARDED_ALLOW_IPS` set to its address.
2. Give every tenant a plan with `calls_per_minute` (F-2).
3. Fix F-1 and F-5 in code, or accept them in writing. Both are fixed in 0.1.1.
4. Have the remote surface reviewed or penetration-tested by someone independent of its
   authors.
