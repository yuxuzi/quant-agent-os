# 0028: Opt-in OAuth sign-in on a firm's own server

- Status: accepted (the owner approved sign-in as opt-in, off by default, on 2026-11-17); extends
  [0013](0013-identity-and-tenancy.md) to a firm's own server, which [0023](0023-no-hosted-service.md) left
  without a command that serves sign-in
- Date: 2026-11-17
- Plan: [0.8.0](../plans/proposals-0.8.md), workstream W2

## Context

Decision 0013 made the server an OAuth 2.1 resource server: tokens from a tenant's identity provider, checked by
introspection, beside API keys. Decision 0023 removed the hosted service that served it, so the token checks stayed
in `quant_platform.identity`, and only a firm that wrote its own `build_http_app` call could use them. `qaos serve`
and `serve_apps` took API keys only, and tokens carried no groups, so per-caller access
([0021](0021-per-caller-access.md)) could not apply to a signed-in user.

A fund that adopts the platform wants its people to sign in through its own identity provider, as Claude and
Cursor connectors expect, and wants its directory's groups to decide who reaches which desk's data. Keys stay for
machines and for teams without a provider.

## Decision

- **Opt-in, off by default.** A sign-in file, named by `QUANT_AGENT_OS_SIGN_IN_FILE` (or `sign_in_file` in
  `config.toml`, or `qaos serve --http --sign-in`), turns it on. Without one, nothing changes: the server takes
  API keys only, needs an enabled key to start, publishes no metadata, and never contacts a provider.
- **The sign-in file names the resource and the providers.** `resource` is this server's URL, which every token's
  audience must be (RFC 8707). Each `[[authorization_servers]]` entry has the issuer, the introspection URL, the
  client id, and `client_secret_env`, the name of the variable that holds the client secret. The secret is read
  from the environment or the home's `.env`, held as a `SecretStr`, and never displayed. An entry may set the
  tenant its users act in (default `local`, the only tenant a firm's server serves), the tenant claim (default
  `qaos_tenant`; empty when the provider sends none), the value that claim must hold when the provider names the
firm otherwise (`tenant_claim_value`), and the groups claim (default `groups`; empty for none).
- **Both interfaces take both credentials.** `/mcp` and REST `/v1` accept an API key, recognised by its `qaos_`
  prefix and never sent to a provider, or a token, checked by the existing `IntrospectionVerifier`. A token must be
  active, unexpired, for this audience, from a configured issuer, and name the configured tenant. Its subject is
  the principal recorded on every run, its scopes map one to one onto the keys' scopes, and it never carries
  `runs:approve`.
- **Groups come from the token.** The groups claim becomes the caller's groups, so `access.toml` rules apply to
  signed-in users as they do to keys. A groups claim that is not a list of strings refuses the token, rather than
  being read as no groups.
- **Fail closed.** A sign-in file that is missing, invalid, has an unknown setting, names an unset secret, uses
  plain http off loopback, lists an issuer twice, or names a tenant the server does not serve, stops the server
  from starting with a message that names the fix. Sign-in with no access file draws a start-up warning, because
  every signed-in user then reaches everything; a sign-in file under stdio draws a warning that it does not apply.
- **RFC 9728 metadata is published** at `/.well-known/oauth-protected-resource/<path>`, and 401 responses from
  `/mcp` and `/v1` point to it in `WWW-Authenticate`.
- **The resource URL's host is admitted** by MCP's DNS-rebinding protection, as `--allowed-host` would admit it,
  because a client that reaches the server by any other name holds a token for another audience.
- **`qaos doctor` reports it** without contacting a provider: whether sign-in is on, whether the file is valid, and
  whether each provider's secret is set.

What 0013 decided still holds: the server issues no tokens and runs no login page, introspection needs no new
dependency, results are cached for at most 60 seconds, and a token is never sent to a provider that could not have
issued it. The bounds on introspection from security review F-1 apply unchanged.

## Why off by default

- **Sign-in widens who can reach the server.** Anyone the provider will issue a token for this audience can call
  it. That is the firm's decision to make, with its provider's configuration in front of it.
- **It adds an outbound dependency.** The server then calls the provider on unauthenticated requests, within
  F-1's bounds. A key-only server makes no outbound call.
- **Most first installs have no provider ready.** Keys work the day the server is installed.

## Alternatives considered

- **Settings for each provider in environment variables.** Several providers, each with five fields, do not fit
  flat variables well, and a TOML file can be mounted read-only beside the access file. Only the secret stays in
  the environment.
- **Local JWT verification.** It saves the round trip but needs a JOSE library; 0013's Q-7 still holds.
- **Groups through a separate mapping file from users to groups.** The provider already holds the directory; a
  second copy would drift from it.
- **Requiring the tenant claim always.** Most providers cannot add a custom claim without work, and on a
  single-tenant server the issuer pin and the audience already bind the token. The claim stays the default, and
  turning it off is explicit.

## Consequences

- A firm's provider needs a registered client for introspection and an audience equal to the resource URL. The
  [deployment guide](../deployment.md#10-sign-in-through-your-identity-provider) says how.
- A revoked token can still be accepted for up to 60 seconds (finding F-4).
- The principal is the token's `sub`. Some providers' subjects are opaque ids, so the audit trail names those ids;
  a claim to choose another principal is not built.
- Tokens still never approve: approving needs an approver key on the approval page (0014).
