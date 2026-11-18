# A pilot at a mock fund

Kestrel Point Capital is a fictional multi-asset fund with three desks (equity long/short, macro, and
commodities), a risk team, and a research team. The 0.8.0 pilot installs the platform at this fund and
records what that takes ([plan](../../docs/plans/proposals-0.8.md)). All its data is fictional.

- [`facility/`](facility/README.md): what the fund already has, written as its own code: a warehouse, a
  legacy Python package, research documents, an identity provider, and desk entitlements.
- `site/` (to come): what the fund writes to adopt the platform.
- `drill.sh` (to come): the fund's day, from installing the wheel to reading the audit trail.

The tests are in `tests/pilot_fund/`.
