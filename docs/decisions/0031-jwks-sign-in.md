# 0031: Sign-in can check JWTs against the provider's keys

- Status: accepted (opt-in, off by default, as 0028; a gap measured by all four mock funds on Azure and AWS;
  the owner delegated the decision on 2026-11-18)
- Date: 2026-11-18
- Extends: [0028](0028-oauth-sign-in-on-a-firms-server.md)

## Context

Decision 0028 checks every token by RFC 7662 introspection, and its alternative "local JWT verification" waited
for a reason to need a JOSE library. Two mock funds on Azure gave it. Microsoft Entra ID and Amazon Cognito have no
introspection endpoint, so every Entra token was refused, and each fund wrote a 70 to 93 line shim in front of the
server that checked the RS256 signature against the provider's keys and rewrote the claims. The deployment guide
named Entra as supported, which was untrue.

Their measurements also showed that introspection's claim reading did not fit these providers:
- the sign-in `resource` must be an https URL and was the only accepted audience, but an Entra `aud` is an
  application ID URI (`api://...`) or a client id GUID, and a Cognito access token has no `aud` at all, only a
  `client_id`;
- scopes arrive in `scp` with dots (`tools.call`), or in Cognito's `scope` with a resource server's prefix;
- groups are GUIDs in Entra, with app roles in a second claim, `roles`; Cognito's are in `cognito:groups`;
- a client-credentials token (a machine) carries `roles`, no `scp`, and no groups, so a machine could not be
  entitled;
- the principal was `sub` or `oid`, so the audit trail showed GUIDs, and the provider's directory (`tid`) was not
  recorded;
- Entra leaves out a user's groups when there are too many, and names a Graph request instead.

## Decision

- **A second way to check a token, chosen per provider.** An `[[authorization_servers]]` entry with
  `verify = "jwks"` is checked by `JwksVerifier` beside `IntrospectionVerifier`, with the same interface. The
  default stays `"introspection"`, existing files read exactly as before, and a file may mix both. A token goes
  only to the verifier whose provider's issuer it names, as 0013 required.
- **Keys.** From `jwks_uri`, or from the `jwks_uri` in the issuer's `/.well-known/openid-configuration`, whose
  `issuer` must equal the configured one and whose address must pass the same https rule as the file. Fetched
  with httpx, kept for an hour, and fetched again for an unknown `kid` at most once every 30 seconds per issuer,
  so unauthenticated requests cannot flood a provider (finding F-1). Only signing keys of types RSA and EC are
  used, and a `kid` published twice is dropped. A fetch that fails, or an answer with no usable key, refuses the
  token; keys already held stay until their hour is up, and a fetch is not tried again within the interval.
- **Signature and header.** PyJWT, which MCP already requires (`pyjwt[crypto]`), so the lock gains no package.
  The algorithms come from the file, default `["RS256"]`, and only RS, PS, and ES families are accepted: never
  `none`, never HS*, so a public key cannot be used as an HMAC secret. The header must have a `kid` and no `crit`,
  and a JWK's declared `alg` must equal the header's. `jku`, `x5u`, and an embedded `jwk` are ignored: keys come
  only from the configured provider.
- **Claims.** `iss` must equal the configured issuer exactly, and `exp` must be present and in the future.
  `nbf` and `iat` must not be in the future, each within `leeway_seconds` (default 30, at most 300). `aud` must
  include one of `audiences`, which is separate from `resource` and any string; a token with no `aud` is accepted
  only when its `client_id` is in `client_ids`; a file must set one of the two lists. `token_use`, when set,
  must equal it, so Cognito's ID tokens are refused where access tokens are needed. A scope, groups, or provider
  tenant claim of the wrong type refuses the token.
- **Claim mapping, all optional.** `principal_claim` (default `sub`, which it falls back to); `scope_claims`
  (default `["scope"]`; each a space-separated string or a list) with `scope_map` renaming to the server's
  scopes, where a mapping to `runs:approve` or an unknown scope refuses the file and a token never carries
  `runs:approve`; `groups_claims` (default `["groups"]`; the union of the lists) with `group_aliases` renaming
  GUIDs to names for `access.toml`; and `provider_tenant_claim`, a claim that must be present whose value is
  added to the principal as `name@value`, so the audit trail names the directory. The tenant claim settings of
  0028 work as before; Entra uses `tenant_claim = ""`, or `"tid"` with `tenant_claim_value`.
- **Machines.** An app role in `roles` entitles a machine through `scope_claims` with `scope_map`, and through
  `groups_claims` for data access.
- **Overage fails closed.** A token with Entra's `_claim_names` or `hasgroups` marker for `groups` is read as
  having none of those groups, with a warning. The server makes no Graph call.
- **Settings the mode would ignore are refused.** Introspection settings with `verify = "jwks"`, or keys-only
  settings with introspection, stop the server from starting, as 0021 and 0028 require of a configuration an
  interface would ignore. Both `groups_claim` and `groups_claims` is refused.
- **Fail closed, and quiet.** Anything uncertain is a refusal. The client sees a 401 either way; the server logs
  why at info level, naming the configured issuer and never the token, and an unknown issuer is not logged at all.

## Why

- **Without it, Entra and Cognito firms need a shim they must trust.** Each fund's shim was security-critical code
  to write and maintain. The server checks it once, tested against the threats below.
- **It is opt-in.** Only a sign-in file that asks for `verify = "jwks"` turns it on, and introspection is
  unchanged.
- **No new dependency.** PyJWT is already installed by `mcp`; the server extra does not list it, because the
  `mcp` extra supplies it.

## Alternatives considered

- **Shipping an Entra-specific verifier.** Entra, Cognito, and other JWT issuers differ only in claim names, which
  settings express. One verifier is less code to trust than one per provider.
- **Calling Microsoft Graph for overage.** It needs a credential with directory permissions and an outbound call on
  the request path. The fix for overage is in the provider (app roles, or filtered groups).
- **Reading the provider's tenant as the server's tenant.** `tenant_claim_value` already does that where wanted;
  the `provider_tenant_claim` setting only records it.
- **A cache of verdicts, as introspection has.** Checking a signature is cheap, and a cache would only delay a key
  withdrawn by the provider.

## Consequences

- **A signed token cannot be revoked.** It is accepted until its `exp`, where introspection can stop one within
  60 seconds. Firms using it should ask the provider for short-lived access tokens, as the deployment guide says.
- **Keys rotate without a restart,** within 30 seconds of the first token with the new `kid`; until then that
  token is refused.
- **The principal in the audit trail changes shape** when `provider_tenant_claim` is set: `name@tid`. Rules keyed to
  a principal name, if a firm adds any, need that form.
- **Some providers do not publish the OAuth metadata MCP clients look for.** That is the client's discovery, not
  the token check; the guide says to try the connector and set it up by hand if it fails.
- **Tests** generate RSA keys, serve fake Entra and Cognito issuers in process, and send tokens through the HTTP
  app. They cover a wrong issuer, audience, expiry, `nbf`, and `iat`; `alg: none`; HS256 signed with the public key
  (algorithm confusion); a tampered payload and signature; another key under a published `kid`; an unknown `kid`
  with and without rotation; a key fetch that fails; overage; and an ID token where an access token is required.
