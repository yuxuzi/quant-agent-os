# 0011: A REST interface over the executor, authenticated by scoped API keys

- Status: accepted (implemented in B-18, 2026-11-01)
- Date: 2026-11-01

## Context

Backlog B-18 asks for a REST API (FastAPI, an optional extra) with API-key authentication,
scopes, and a principal in audit events. Web apps and non-Python clients cannot use MCP stdio
or the Python SDK. Unlike stdio, an HTTP port can be reached by anyone on the network, so
the interface must authenticate every caller and must not widen what the executor allows
(decisions 0002 and 0005).

## Decision

- **An adapter, not a product.** `quant_agent_os.api` maps HTTP onto the same `ToolExecutor`
  and `RunRecorder` as MCP, with no business logic (decision 0002). FastAPI and uvicorn come
  from the optional `api` extra, and importing the package without the extra fails with an
  install hint. The product CLIs serve their own tools (`quant-research serve-api`), as with
  `serve-mcp`.
- **Routes.**
  - `GET /healthz` returns only `{"status": "ok"}` and needs no key.
  - `GET /v1/tools` lists the same definitions as MCP `tools/list` (scope `tools:list`).
  - `POST /v1/tools/{name}/call` takes `{"arguments": {...}}` and needs scope `tools:call`.
    It returns the call record: run id, call id, status, output, error, and data versions.
    A failure also carries the hint and retryable flag MCP reports (added in B-30).
  - `GET /v1/runs` and `GET /v1/runs/{run_id}` read the run store (scope `runs:read`).
- **Status codes.**
  - Every executed call returns 200 with its `status`, as MCP returns `isError`, so a tool
    failure is never confused with a transport failure. The output equals MCP's
    `structuredContent` and the direct executor output, which a contract test checks.
  - A tool that is not listed returns 404 and records nothing.
  - 401 means a missing or unknown key, 403 a missing scope, 413 an oversized body, and 422
    a body that is not `{"arguments": {...}}`.
- **API keys.**
  - A key is `qaos_` followed by 32 random bytes (URL-safe base64), sent as
    `Authorization: Bearer <key>`.
  - The server stores only its SHA-256, in a TOML keys file with a `principal` and `scopes`
    per key. The key is high-entropy, so a plain hash is enough, and comparison is
    constant-time.
  - `quant-agent-os api-key new` prints a key once and appends its hash to the file,
    created with mode 0600. Deleting an entry, or setting `disabled = true`, revokes the key
    at the next start.
- **Scopes narrow; they never widen.** The scopes are `tools:list`, `tools:call`, and
  `runs:read`. The server's `PermissionPolicy` comes from the same settings as `serve-mcp`, so
  it is read-only by default. A consequential call returns `approval_required`, and there is
  no approval route: the operator approves through the SDK on the same run store
  (decision 0005). No key, scope, or client can create an approval.
- **Principal in the audit trail.** Runs and audit events gain an optional `principal`
  (run-store schema 3; older stores gain the column in place). REST runs record actor `rest`
  and the key's principal.
- **Safe defaults.**
  - The server refuses to start without at least one enabled key.
  - It binds `127.0.0.1` unless told otherwise, and warns on any other address that TLS
    belongs in a reverse proxy.
  - Request bodies are capped at 5 MiB, which is configurable.
  - CORS is off, and error bodies never echo the key.

## Consequences

- Non-Python clients get the same tools, records, and permissions as MCP clients.
- **Out of scope for now:**
  - OIDC;
  - rate limiting, left to the reverse proxy;
  - MCP over HTTP;
  - per-principal isolation of runs. A `runs:read` key reads every run in the store, which
    can hold retrieved passages, so grant it like access to the corpus.
- FastAPI 0.143 adds one new transitive package, `opentelemetry-api`. Both are locked under
  the two-week cut-off. starlette, uvicorn, and httpx were already locked through the `mcp`
  extra.
