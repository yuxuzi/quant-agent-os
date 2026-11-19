# 0030: A signed caller for a firm's service, and standing approvals for named tools

- Status: accepted (the owner approved both on 2026-11-18, from the 0.8.0 pilot drill's findings P7 and P13);
  builds on [0005](0005-approval-gates.md), [0020](0020-a-firms-own-server.md),
  [0021](0021-per-caller-access.md), [0024](0024-registering-a-firms-functions.md), and
  [0028](0028-oauth-sign-in-on-a-firms-server.md)
- Date: 2026-11-18
- Plan: [0.8.0](../plans/proposals-0.8.md), iteration two

## Context

The drill at the mock fund served the fund's stateful risk engine as a service behind `FirmApp.call_service`
(decision 0024). Two things went wrong.

- **P7: the service could not tell who was calling.** A facade in the server's process can ask
  `current_caller()` (decision 0021), but a service only saw an HTTP request with, at most, the platform's own
  bearer token. It could not scope an answer to the caller, so a PM could not be given their own desk's VaR;
  the fund granted the risk tools to the risk group only.
- **P13: a standing approval covered every tool.** The risk tools need `network`, which needs an approval for
  each call (decision 0005), and MCP clients cannot approve. The only unattended setting,
  `QUANT_AGENT_OS_PREAPPROVED_PERMISSIONS='["network"]'`, approved `network` for every tool on the server,
  including any added later, such as search with a hosted embedder.

## Decision

### A signed caller assertion, opt-in per tool

- **Registration.** `call_service(..., forward_caller=True, caller_secret_env="RISK_CALLER_SECRET")`. Off by
  default.
- **What is sent.** Each call carries `X-QAOS-Caller: v1.<payload>.<signature>`. The payload is base64url,
  without padding, of compact JSON with sorted keys: `sub` (principal), `ten` (tenant), `grp` (groups, sorted),
  `tool`, `iat` and `exp` (whole seconds since the epoch, 60 apart), and `nonce` (128 random bits as hex). The
  signature is base64url, without padding, of HMAC-SHA256 over the ASCII bytes `v1.<payload>`, keyed with the
  secret's UTF-8 bytes. The version prefix lets a later format be told apart.
- **The secret** is read from `caller_secret_env` at each call, so it can be rotated without a restart; it must
  be at least 32 bytes, and it is never displayed, logged, or recorded. Nor is the header.
- **Fail closed.** A call with no bound caller (stdio, the SDK, a test that names none) is refused before any
  request is sent, rather than sending an anonymous assertion. So is a call whose secret is unset or too short.
- **Refused at registration:** `forward_caller` without `caller_secret_env`, `caller_secret_env` without
  `forward_caller` (a setting that would be ignored), a `caller_secret_env` that is not a variable's name (it
  may be the secret itself), one equal to `token_env` (the secret would go out as the token), and plain http
  off loopback, as for a token.
- **The verifier.** `quant_agent_os.caller_assertion`, re-exported from `quant_agent_os.registration`, has
  `verify_caller(header, secret, *, tool=None, now=None, leeway=10)`. It checks the version, the signature
  with `hmac.compare_digest`, the payload's fields, that the assertion is neither expired nor issued in the
  future, that it claims to live at most 300 s, and the tool. It returns a `VerifiedCaller` or raises a
  subclass of `CallerAssertionError` (`MalformedAssertionError`, `UnsupportedVersionError`,
  `BadSignatureError`, `ExpiredAssertionError`, `WrongToolError`). The module uses only the standard library,
  so a service can import it from the wheel or copy it; [deployment](../deployment.md#8-add-your-own-tools)
  shows a 30-line standard-library verifier that a test keeps in step. `sign_caller` signs one for a firm's
  tests, and `check_tool` and `check_app` take `caller=`.
- **The converter** drafts a commented `TODO(review)` hook in the generated service (decision 0029).

### Standing approvals for named tools

- **The setting.** `QUANT_AGENT_OS_PREAPPROVED_TOOLS='{"network": ["risk_desk_var", "risk_stress"]}'`, in the
  environment or the home's `.env`, like every permission setting; `config.toml` refuses it. In code,
  `PermissionPolicy(preapproved_tools={Permission.NETWORK: frozenset({...})})`.
- **Only the named tools** run without a per-call approval; another tool needing the same permission still
  needs one. The record of each such call says `standing:<permission>`, as before.
- **Validation.** Names must be exact: a pattern or an empty name is refused, because it would approve tools
  added later. An unknown permission, or one that needs no approval, is refused. At start-up, `qaos serve` and
  `serve_apps` warn about a named tool the server does not serve, or one that does not declare the permission,
  because it approves nothing. It grants nothing: the permission must also be granted.
- **The server-wide form stays**, for compatibility, and warns at start-up that it covers every tool the server
  serves. `qaos doctor` shows both forms.
- **Never a default.** Both are empty by default, and only the operator's environment sets them. No tool,
  client, or model output can.

## Why

- **HMAC over a shared secret** needs only the standard library (no JOSE dependency, as decision 0013's Q-7
  still holds) and a few lines in any language on the service's side. The platform and the firm's service are
  run by the same firm, so a shared secret is the simplest key distribution that works.
- **A new statement, not the client's token.** Forwarding the client's own token would be token passthrough,
  which the MCP security practices forbid, and an API key has no meaning to the service. The platform asserts
  only the caller it verified.
- **Short-lived and bound to the tool,** so a leaked header is useful for about a minute and only at one
  endpoint, without the service keeping state.
- **Per-tool approvals by exact name** keep the operator's decision as narrow as the drill needed, and visible
  in one setting.

## Alternatives considered

- **Forwarding the principal and groups in plain headers.** Anyone who could reach the service could claim to
  be anyone.
- **Signed JWTs (HS256).** The same cryptography with a header the service must parse and an `alg` it must pin;
  a library would be needed to do it well.
- **Mutual TLS with the caller in a client certificate.** It authenticates the platform, not the caller.
- **Approvals per tool and per caller.** Not needed by the drill; a firm can narrow who reaches a tool with the
  access file (decision 0021).
- **Removing the server-wide form.** Existing servers would stop working on upgrade; it stays, with a warning.

## Consequences

- A firm's service can scope its answers, so a PM can be given their own desk's VaR. The access file still
  decides who reaches the tool at all.
- A firm sets one more secret per service, on both sides.
- The deployment, configuration, and security pages describe both; `qaos doctor` shows the standing approvals.

## Open questions for the next security review

- **Replay within the minute.** The assertion is not bound to the request's arguments or to one use. A service
  that must refuse a replay keeps nonces until they expire. Binding a hash of the request's method, path, and
  body would close it, at the cost of a canonical form the service must reproduce.
- **One secret, many endpoints.** The tool check stops an assertion for one tool being used at another only if
  the service passes `tool=`. A secret per service limits the damage of one leaking.
- **Clock skew.** The 10-second leeway and 60-second lifetime assume NTP on both hosts.
- **Who holds the secret.** Anyone who can read the server's environment can sign any caller. That is the same
  trust as the server's own keys file, but it is now trusted by another system too.
