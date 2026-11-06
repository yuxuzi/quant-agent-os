# 0019: The Business Source License, from the next release

- Status: accepted (decided by the owner, 2026-11-06; counsel to review before first commercial use)
- Date: 2026-11-06
- Closes: open question O-4
- Amends: [0015](0015-metering-quotas-and-entitlements.md) (Q-18) and
  [0017](0017-hosted-behind-a-command-group.md)

## Context

The customer is a firm hosting its own server, and the project needs to be able to charge for production use.
MIT permits that use for free. Most source-available licences do too: the Elastic License 2.0 and the Functional
Source License restrict hosted or competing services, while a firm's own internal use stays allowed.

Changing now is cheap: the repository has one human author, no forks, and no outside contributions.

## Decision

- **The Business Source License 1.1**, with no Additional Use Grant. Anyone may copy, modify, redistribute, and
  make non-production use of the code. Production use needs a commercial licence.
- **Change Date:** the fourth anniversary of each version's first public distribution, the longest the licence allows.
- **Change License:** Apache 2.0.
- **What stays MIT:** `skills/`, `clients/`, and `src/quant_app_factory/packs/`. Skills are copied into clients, and
  a generated project must carry only the licence its owner chooses.
- **One licence for the rest.** One wheel holds every product ([decision 0018](0018-one-wheel.md)), so editions are
  contract terms and not separate packages. No code checks a licence key.

## Consequences

- Versions up to v0.2.0, and earlier commits on `main`, stay MIT. This cannot be withdrawn.
- The project is source-available, and must not be described as open source.
- Outside contributions need a contributor agreement first.
- `pyproject.toml` declares `BUSL-1.1 AND MIT`, and the wheel carries both licence files.
