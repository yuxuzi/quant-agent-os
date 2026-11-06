# 0017: Hosted features sit behind a command group

- Status: accepted
- Date: 2026-11-05
- Amends: [0012](0012-remote-mcp-over-http.md), [0013](0013-identity-and-tenancy.md),
  [0014](0014-approvals-in-the-client.md), [0015](0015-metering-quotas-and-entitlements.md)

## Context

The next phase serves a firm or team that hosts its own server
([simplification plan](../plans/archive/simplification.md)). About 1,480 lines of tenants, plans, metering,
OAuth, and the approval page serve a vendor with many customers, and they sat in the default path:
`serve-http` had nine options with rules that depended on each other.

The plan called for a `hosted` extra. An extra cannot do this job: it adds dependencies, and it
cannot remove modules from a wheel. This code needs no dependencies of its own, so the extra would
be empty and would keep nothing out.

## Decision

- `quant_platform.hosted` holds the tenants file, plan limits, usage export, OAuth, and the approval
  desk and page. These modules leave Core.
- `quant-platform hosted` runs them: `serve-http`, `tenant`, and `usage`. The default `serve-http`
  takes one team's store, datasets, and API keys.
- A test checks that the default commands load no hosted module beyond the command definitions.
- Core keeps small hooks: the `UsageGate` protocol, `Refusal`, a per-caller approval gate on
  `add_mcp_route`, and the `tenant` column, whose default `local` avoids a migration.
- Usage events are still recorded on every call, and `Tenancy` stays as the seam `serve-http` uses.

## Consequences

- No permission widens. On the default server a consequential call returns `approval_required`, and
  an operator approves it through the SDK. It no longer offers an approval page, and by default it
  serves only read-only tools.
- Isolation and approval tests still run, against the moved code.
- A firm that wants single sign-on must use the hosted command for now. If the first design partner
  asks, split OAuth out on its own.
- The code still ships in every install, so this is a fence for attention and attack surface at
  start-up, not a licence boundary. [Decision 0019](0019-business-source-license.md) licenses all of it together.
