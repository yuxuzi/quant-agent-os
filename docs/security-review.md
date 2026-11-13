# Security review of the remote surface

- Status: second review, findings open. **An AI agent's review, not a penetration test.** It does not replace
  an independent review, which is recommended before production use and before a public endpoint.
- Dates: 2026-11-03 at `b03b5a0` (the remote surface, B-36 P7.4,
  [decision 0015](decisions/0015-metering-quotas-and-entitlements.md)), and 2026-11-06 at `23583bf` (a firm's own tools,
  per-caller access, and the operator commands, [below](#a-firms-own-tools-and-per-caller-access)).
- Method: reading the code and its tests against the
  [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
  and the [MCP security best practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices).
  The second review also ran real servers in the configurations most likely to fail open.
- **Since then:** [decision 0023](decisions/0023-no-hosted-service.md) removed the multi-tenant server, plans, and
  usage export. Rows below about tenants, plans, and `qaos hosted serve` describe code that no longer ships; OAuth
  and the approval page moved unchanged to `quant_platform/identity/`.

The general model is in [security.md](security.md). This page checks it against the code and
lists what is still open.

## The surface

`qaos serve --http` serves everything below except the approval page and OAuth metadata, which only a firm's own server can add, from `quant_platform.identity`. Local stdio (`qaos serve`) is
out of scope: it trusts the user who started it.

| Route | Who may call it | Guarded by |
| --- | --- | --- |
| `POST /mcp` (Streamable HTTP, stateless) | API key or OAuth token, scope per method | `mcp/http.py`: bearer middleware, `caller_of`, `tenant_route` |
| `/v1/tools`, `/v1/tools/{name}/call`, `/v1/runs` | API key with the route's scope | `api/app.py`: `requires(scope)`, `surface_of` |
| `GET`/`POST /approve/{id}` | Anyone with the link; deciding needs an approver key | `quant_platform/identity/approval_page.py`, `approval_desk.py` |
| `/.well-known/oauth-protected-resource/mcp` | Anyone (public metadata) | MCP SDK |
| `/healthz` | Anyone; returns `{"status": "ok"}` only | — |

Every tool call, from any route, then passes the executor's checks in order: registered tool,
permission policy, input validation, plan limits, approval, timeout, and output size.

## OWASP API Security Top 10 (2023)

| Risk | What the code does | Evidence | Verdict |
| --- | --- | --- | --- |
| **API1 Broken object-level authorization** | The tenant comes only from the verified credential. Each tenant has its own store, datasets, and registry; run reads are filtered by tenant; a guessed resource URI resolves inside the caller's tenant. Within a tenant, an access policy limits tools and datasets per caller, and a refused dataset answers as a missing one. Any `runs:read` key reads every principal's runs, and the corpus is all or nothing per caller. | `tests/contract/test_mcp_http.py`, `tests/server/test_platform_authorization.py` (release gates) | Pass, with F-8 and F-9 |
| **API2 Broken authentication** | Keys have 256 random bits, are stored as SHA-256 hashes (file mode 0600), and are compared in constant time against every hash. OAuth tokens are introspected and must be active, unexpired, for this server's audience, from a pinned issuer, and name a pinned tenant. Tokens are never sent to a server that could not have issued them. | `tests/unit/test_api_key_cli.py`, `tests/server/test_oauth.py` | Pass, with F-1 and F-4 |
| **API3 Broken object-property-level authorization** | Inputs are validated against each tool's model, and REST rejects unknown body keys. Outputs are validated against the output model. Stored arguments and outputs are redacted; usage events and telemetry carry sizes and labels only. | `tests/unit/test_runs.py`, `tests/unit/test_telemetry.py` | Pass |
| **API4 Unrestricted resource consumption** | Bodies are capped at 5 MiB (approval forms at 8 KiB), each tool has a timeout and an output cap, an executor runs at most 16 calls at once (`busy` beyond that), and a program a tool runs is killed at its timeout. Unauthenticated requests are not limited, and a timed-out tool thread keeps running. | `tests/contract/test_rest_api.py`, `tests/unit/test_executor.py` | Partial: F-1, F-2 |
| **API5 Broken function-level authorization** | Scopes are checked per MCP method and per REST route. No route can approve: approving needs `runs:approve`, which no key holding `tools:call` and no OAuth token can carry. Operator actions (ingestion, keys, tenants, usage export) are CLI commands, not routes. | `tests/server/test_approval_page.py` | Pass |
| **API6 Unrestricted access to sensitive business flows** | A consequential call needs a per-call approval bound to the run, tool, and input hash, decided once by a different person (four eyes except `local`). Five failed attempts deny a request; each address may submit 30 forms a minute. Remote servers never write users' files (`create_app` is not served over HTTP). | `tests/server/test_approval_page.py` | Pass, with F-3 |
| **API7 Server-side request forgery** | No tool takes a URL. Outbound calls go only to operator-configured hosts: the introspection URLs in the tenants file and the hosted embedder. | Code reading: `quant_platform/hosted/oauth.py`, `quant_research/infrastructure/remote_embedding.py` | Pass |
| **API8 Security misconfiguration** | Binds loopback by default and warns otherwise; no CORS, docs pages, server header, or date header; DNS-rebinding protection on `/mcp`; the approval page sends a strict CSP, `no-store`, `no-referrer`, and `DENY` framing. A failed tool's exception text is returned to the caller. | `tests/contract/test_mcp_http.py`, `tests/contract/test_rest_api.py` | Pass, with F-5 |
| **API9 Improper inventory management** | Tools are registered explicitly, never discovered. Each has a semver version checked against `scripts/tool-versions.lock`; `server_info` and `qaos://server/info` list them; deprecations name a replacement and a removal date. | `tests/server/test_tool_lock.py` | Pass |
| **API10 Unsafe consumption of APIs** | Introspection responses are type-checked claim by claim, and failures refuse the token. Retrieved passages are marked `"trust": "untrusted"`, and no tool output reaches the platform's own planner. | `tests/server/test_oauth.py`, research contract tests | Pass |

## MCP security best practices

| Practice | Status |
| --- | --- |
| **No token passthrough.** The server accepts only tokens issued for it (audience equals `--resource-url`) and never forwards a client's token to another service. Tools never see credentials. | Met |
| **Confused deputy.** The server is a resource server only. It does not proxy OAuth to a third party or hold a static client id for users, so there is no consent to bypass. | Not applicable |
| **Session hijacking.** The transport is stateless: there are no session ids to steal or inject, and every request is authenticated on its own. | Met |
| **Scope minimization.** Four scopes; OAuth tokens get only those they name, never `runs:approve`. The policy is read-only by default per tenant. | Met |
| **DNS rebinding and local exposure.** `Host` and `Origin` must be loopback or an `--allowed-host`. | Met |
| **Untrusted tool output (prompt injection).** Passages are marked untrusted, server instructions say not to follow them, and consequential calls still need a person's approval. The client's own behaviour is outside the server's control. | Met as far as a server can |

## A firm's own tools and per-caller access

Reviewed against [decisions 0020](decisions/0020-a-firms-own-server.md) and
[0021](decisions/0021-per-caller-access.md), with `qaos eval`, `doctor`, `runs prune`, `runs export`, `backup`, and `restore`.

| What was checked | Result | Evidence |
| --- | --- | --- |
| The caller comes only from the verified credential, and reaches the tool's thread | Holds, under parallel callers with different rights | `test_parallel_callers_never_see_each_others_identity_or_data` |
| Anything uncertain is a refusal: no caller, a policy that raises, a missing or invalid file | Holds. A bad file also stops the server starting, tried for real | `test_a_failing_access_policy_refuses`, `test_an_invalid_file_allows_nothing_until_it_is_fixed` |
| A refused dataset cannot be told from a missing one, by text | Holds, over MCP and REST. Timing was not measured: a refused read skips the disk | `test_a_refused_dataset_answers_exactly_as_a_missing_one` |
| Nested calls, resources, tool lists, and `server_info` follow the caller's rights | Holds | `test_research_reports_nested_read_keeps_the_caller`, `test_a_resource_follows_the_same_rights` |
| A configured access file always applies | **Did not hold, now fixed:** F-10 | Tried on a real `qaos hosted serve` |
| Groups on keys always mean something | **Did not hold, now warned:** F-11 | Tried on a real `qaos serve --http` |
| A tool's declared permissions limit what it can do | **Does not hold, and cannot in this design:** F-6 | Tried with a tool that declares only `read_local_data` |
| Operator commands are not reachable by a caller | Holds. They are commands, and no tool or route offers them | `tests/server/test_tool_lock.py` lists every tool |
| `doctor`'s output, which goes in support tickets, holds no secrets | **Did not hold for an invalid keys file, now fixed:** F-12 | `test_doctor_output_holds_no_secrets` |
| Pruned text is gone from the files, and audit events stay append-only | Holds, with the server stopped. A busy server can delay the log, and the command warns | `test_pruned_text_is_gone_from_the_files`, which fails without `secure_delete` |
| Audit export holds no content | Holds | `test_export_holds_no_content_and_honours_the_window` |
| `restore` cannot destroy data on a bad archive | Holds on empty, garbage, and truncated archives | `deploy/smoke-test.sh`, in CI |

## Findings

Severity for F-1 to F-5 is for a public, multi-tenant endpoint. On a loopback or private team server, F-1
and F-2 matter much less. Severity from F-6 on is for one firm's server.

| Id | Severity | Finding | Recommendation |
| --- | --- | --- | --- |
| **F-1** | Medium | **Unauthenticated amplification through introspection.** A JWT-shaped token whose unverified `iss` names a configured provider triggers one introspection request, and failures are not cached. Anyone can make the server send requests to a tenant's identity provider at the rate they send requests. Plans do not apply before authentication. | Rate-limit unauthenticated requests in the reverse proxy (already required). Then cache refusals for a short time and bound concurrent introspections per provider. **Fixed in 0.1.1:** refusals are cached for 30 s, and each provider gets at most 600 introspections a minute and 8 at once. |
| **F-2** | Medium | **Partly fixed.** A Python thread cannot be killed, so a timed-out tool still keeps its thread until it returns. Each executor now runs at most 16 calls at once, and a timed-out call keeps its slot, so threads can no longer accumulate without limit. When every slot is taken, calls return `busy` (REST: 503 with `Retry-After`). Tools that hang for good reduce capacity until a restart. | Run slow tools in a process that can be killed. |
| **F-3** | Low | **The approval page is a capability URL.** Anyone with the link sees the tool, the redacted arguments, and the requester's principal (not the key). Links can surface in browser history, proxy logs, and auto-instrumented HTTP spans. Deciding still needs an approver key. | Keep the 15-minute expiry. Document that telemetry and proxy logs see these paths (done in deployment.md). Consider requiring an approver key to view as well. |
| **F-4** | Low | **Partly fixed.** API keys can expire, and the server re-reads the keys file without a restart. An OAuth token can still stay accepted for up to 60 seconds after it is revoked (the introspection cache). | Accept the OAuth window, or shorten the cache. |
| **F-5** | Low | **Failure messages echo exception text.** A tool that raises returns `<ExceptionType>: <message>` to the caller, which can include internal details such as file paths. | Return a generic message with the call id, and keep the detail in the run record. **Fixed in 0.1.1:** `ValueError` and `LookupError` messages pass through; everything else is generic, with the text in `error_detail`. |

| **F-6** | Medium | **Declared permissions are checked, not enforced.** The executor refuses a tool whose declared permissions are not granted, and nothing stops a tool doing more than it declares. A firm's tool that declared only `read_local_data` wrote a file and opened a socket, in the server's process, where it can also read every dataset and the run store. Platform tools are written to their declarations; a firm's own are the firm's to review. | Treat a firm's tools as server code, and limit the container's network where they need none. Running tools in a separate process (F-2) would be the place to enforce more. |
| **F-7** | Low | **The audit trail is not tamper-evident.** Database triggers reject changes to audit events, but whoever can write the file can remove them. | Export the trail to a system the host's operators do not control. |
| **F-8** | Low | **`runs:read` reads every caller's runs,** with what they retrieved and computed, which access rules do not filter. By design, and documented. | Give it only to auditors. |
| **F-9** | Low | **The corpus is all or nothing per caller,** so access rules cannot separate research. By design ([decision 0013](decisions/0013-identity-and-tenancy.md)), and documented. | Run one server per corpus. |
| **F-10** | Medium | **Fixed.** `qaos hosted serve` ignored a configured access file and started, so every key reached every tool while the operator believed otherwise. | It now refuses to start. Per-caller access under `qaos hosted serve`, with groups on OAuth tokens, is not built. |
| **F-11** | Low | **Partly fixed.** Keys with groups on a server with no access file reach everything. That stays, since no file means no policy, but it was silent. | `serve_http` and `qaos doctor` now warn. |
| **F-12** | Low | **Fixed.** An invalid API keys file put key hashes in its error, which reached logs and `doctor`. | It names the field and the problem only. |
| **F-13** | Low | **A prune records no person.** Its audit event has actor `cli`, because operator commands trust the host, so who ran it is not on record. | Run operator commands through your own change process, and keep the host's own logs. |

No finding allows a cross-tenant read, or an approval without an approver key. Through the platform's own tools, none
allows a read outside a caller's access rules once they apply. A firm's own tools are outside that claim (F-6).

## Before production use by a firm

1. Have it reviewed or penetration-tested by someone independent of its authors. This page is not that.
2. Put a reverse proxy in front, with TLS and rate limits.
3. Review the firm's own tools as server code (F-6), and write its access file before issuing keys (F-11).
4. Decide who holds `runs:read` (F-8), how long runs are kept, and where the audit trail is exported (F-7).
5. Agree who is responsible for what, starting from [the table in the security model](security.md#who-is-responsible-for-what).

## Before a public endpoint

1. Put a reverse proxy in front: TLS, a rate limit on unauthenticated requests (F-1), and
   `FORWARDED_ALLOW_IPS` set to its address.
2. Give every tenant a plan with `calls_per_minute` (F-2).
3. Fix F-1 and F-5 in code, or accept them in writing. Both are fixed in 0.1.1.
4. Have the remote surface reviewed or penetration-tested by someone independent of its
   authors.
