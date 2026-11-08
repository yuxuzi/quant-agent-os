# 0023: No hosted service; each firm runs its own installation

- Status: accepted (direction from the owner, 2026-11-06)
- Date: 2026-11-08
- Supersedes: [0017](0017-hosted-behind-a-command-group.md)
- Amends: [0013](0013-identity-and-tenancy.md), [0015](0015-metering-quotas-and-entitlements.md)

## Context

[Decision 0017](0017-hosted-behind-a-command-group.md) kept tenants, plans, usage export, OAuth, and the approval
page behind `qaos hosted`, for a vendor serving many customers. That vendor will not exist. A fund will not trust a
small third party with its positions, signals, or research, so every customer runs its own installation, on its
own infrastructure, and the vendor operates no service. Code for a multi-tenant service is then attack surface,
documentation, and tests with no user, and the tutorial taught it as if it were the product.

## Decision

- **Remove** the `qaos hosted` command group, the tenants file, plans and their limits, and usage export.
- **Keep OAuth and the approval page,** moved unchanged to `quant_platform.identity`. A firm may need sign-in
  through its identity provider and a browser approval page on its own server. Their security behaviour does not
  change here. No command serves them yet: wiring them into `qaos serve` is a security change, to be decided when
  a firm asks.
- **Keep the small hooks in Core:** the `UsageGate` protocol and `quota_exceeded`, the `tenant` column (default
  `local`), and the `Tenancy` seam. Removing them would change the run store's schema and the executor for no
  benefit to a customer.
- **A firm that needs separate desks behind an information barrier runs one installation for each.**

## Consequences

- `qaos serve --http` is the only server a firm runs. Per-caller access ([decision 0021](0021-per-caller-access.md))
  is how it gives people different tools and datasets.
- The approval page and OAuth have tests but no command. Until one serves them, a consequential call on the default
  server is approved through the SDK.
- Usage events are still recorded on every call, and `qaos runs export` gives a firm its audit trail.
- The isolation tests for tenants go with the tenants. Per-caller access tests are the release gate.
