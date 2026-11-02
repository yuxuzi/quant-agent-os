# 0014: Approving a consequential call without leaving the client

- Status: accepted (approved by the user 2026-11-02, with Q-11 to Q-13 as proposed)
- Date: 2026-11-02
- Plan: [agent-surface P6](../plans/agent-surface.md), backlog B-35. Builds on
  [0005](0005-approval-gates.md) (approval gates), [0012](0012-remote-mcp-over-http.md)
  (remote MCP), and [0013](0013-identity-and-tenancy.md) (tenants).

## Context

A call that needs a consequential permission returns `approval_required` and waits
(decision 0005). Today an operator can approve it only through the SDK or the CLI, on the
machine that holds the run store. An MCP client therefore hits a dead end: the user sees "ask
your operator", leaves the client, and the call never resumes in the conversation. That is
why `create_app` and `check_app` are not served over MCP at all.

The rule that must survive any fix: **neither a model nor a client can approve.** A tool's
output or a retrieved passage can carry injected instructions, and an MCP client runs the
model's tool calls. So an approval must come from a person, through a channel the model does
not drive.

The MCP spec has two ways to involve the user:
- **Form elicitation.** The server asks the client to show a question, and the client returns
  the answer. The server must trust the client to have asked a person.
- **URL elicitation.** The server tells the client to send the user to a URL, then retry.
  The person acts in a browser, outside the agent loop. The SDK has the
  URL-elicitation-required error (−32042), which works on our stateless HTTP transport.

## Decision

### Remote servers: URL elicitation to a server-hosted approval page

1. **A pending call becomes an approval request.** When an HTTP MCP call returns
   `approval_required`, the server records an approval request bound to the tenant, the run,
   the tool, the validated input's SHA-256, and the requesting principal. Its id has 128
   random bits. It expires after 15 minutes and can be decided once.
2. **The client is sent to approve it.** The call fails with the URL-elicitation-required
   error. That error carries `https://<server>/approve/<request-id>` and a message saying what
   needs approving. A client without URL elicitation gets the existing `approval_required`
   result, and its hint now includes the URL.
3. **The approval page shows exactly what will run.** It shows the tool, its permissions, the
   redacted arguments, the input hash, the requester, and the expiry. The page has no
   scripts, sends a strict Content-Security-Policy, uses no CORS, and protects its form with a
   per-request token.
4. **Only an approver can approve.** A new scope, `runs:approve`, marks approver credentials.
   - **Approver keys are separate from agent keys.** `api-key new` refuses a key that has both
     `runs:approve` and `tools:call`, so the key an agent's client holds can never approve.
   - **How the approver signs in.** The page asks for an approver key; the browser sends it
     once, in a form post over TLS, and it is never stored. OAuth sign-in on the page is later
     work (Q-11).
   - **Same tenant only.** An approver approves only within their own tenant.
   - **Four eyes, by tenant.** With `separate_approver = true` (the default for every tenant
     but `local`, Q-12), an approver cannot approve their own principal's request.
5. **The retry runs in the original run.** After approval, the client retries the identical
   call. The server finds the waiting run for the same tenant, principal, tool, and input hash
   (it is unexpired and approved) and resumes it. The approval's run id, tool, and input hash
   all match, as decision 0005 requires, and the call runs once. A changed argument is a
   different hash, so it gets a new approval request.
6. **Everything is audited.** The request, the approval or denial, the approver's principal,
   and its use are audit events on the run.

### Local stdio: form elicitation, only when the operator opts in

- `serve-mcp --confirm-in-client` lets the server ask the client to confirm a consequential
  call (form elicitation), because on stdio the operator and the user are the same person on
  the same machine.
- It is off by default. It is never available over HTTP, because a remote server cannot know
  that a person answered.
- A "yes" is recorded as an approval with `approved_by = "client-confirmation"`, so the audit
  trail shows the difference.

### App Factory over MCP

- Locally with `--confirm-in-client`, `serve-mcp` serves `create_app` and `check_app`. Each
  call is confirmed in the client, and the files are written on the user's machine.
- Remote servers never write the user's files (plan principle 5). `create_app` is not served
  over HTTP. `check_spec` keeps returning the spec and the `quant-factory create` command to
  run locally.

## Alternatives considered

- **Form elicitation everywhere.** Simplest, but a remote server would have to trust every
  client's UI to have asked a person. That is the gap decision 0005 exists to close.
- **An approval route in REST or MCP.** Any route the agent's credential can reach is a route
  the model can reach. Decision 0011 refused it for that reason.
- **Notify an approver out of band (email, Slack).** Useful later for four-eyes. It needs
  notification infrastructure, and the URL already lets a requester forward the link.

## Consequences

- An approved consequential call resumes in the conversation, without leaving the client.
  Local App Factory works end to end over MCP.
- **A new scope and a new page.** The page is a new unauthenticated-until-submitted surface;
  the contract tests must cover expiry, single use, the CSRF token, cross-tenant and
  self-approval refusals, and that no key or argument appears in the page's HTML, logs, or
  errors.
- **Approver keys need managing.** Teams that want in-client approval must issue them.
  Without one, approvals stay operator commands, as today.
- **Hardening the page later.** OAuth sign-in on the approval page (Q-11), and rate limits on
  the page (P7).

## Resolved questions

- **Q-11.** An approver key in a form post now; OAuth sign-in on the page later.
- **Q-12.** Four eyes is required for every tenant but `local`, and configurable per tenant.
- **Q-13 (as proposed).** An approval request lives 15 minutes.

## Implementation notes (2026-11-02)

- **Where it lives.**
  - `quant_agent_os.approval_desk.ApprovalDesk` opens, decides, and resumes requests.
  - `quant_agent_os.api.approval_page` serves the page.
  - The run store keeps requests (schema 4, the `approval_requests` table).
  - `build_server` takes an approval gate per request (HTTP) or `confirm_in_client` (stdio).
  - `quant-platform serve-http --approval-url` and `quant-factory serve-mcp
    --confirm-in-client` turn them on; both are off by default.
- **Resuming with the real arguments.** Stored arguments are redacted, and resuming from them
  would change the input hash of any call with a secret-named argument, so it could never
  match its approval. `RunRecorder.resume` now takes the original steps, which the desk and
  the stdio confirmation pass. The SDK's `Platform.resume` still resumes from the stored
  arguments, so a call with a secret-named argument cannot be resumed there. That is a known
  limitation, left for a follow-up.
- **Messages.** A client without URL elicitation sees the error message, which carries the
  approval URL.
- **For decision 0013's Q-7.** PyJWT with the crypto extra already comes with the `mcp` extra,
  so local JWT verification would not need a new dependency after all.
