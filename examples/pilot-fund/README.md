# A pilot at a mock fund

Kestrel Point Capital is a fictional multi-asset fund with three desks (equity long/short, macro, and
commodities), a risk team, and a research team. The 0.8.0 pilot installs the platform at this fund and
records what that takes ([plan](../../docs/plans/proposals-0.8.md)). All its data is fictional.

- [`facility/`](facility/README.md): what the fund already has, written as its own code: a warehouse, a
  legacy Python package, research documents, an identity provider, and desk entitlements.
- [`site/`](site/kestrel_server.py): what the fund writes to adopt the platform: its server
  (`kestrel_server.py`), the warehouse adapter (`warehouse_store.py`), the legacy tools the converter
  drafted and a developer finished (`legacy.toml`, `legacy_tools/`), its sign-in file, its settings
  (`kestrel.env`), and its agreed cases (`acceptance.toml`).
- [`drill.sh`](drill.sh): the fund's day, from building the wheels to reading the audit trail, timed,
  with `drill_client.py` working as each of the fund's people.

The [pilot runbook](../../docs/pilot-runbook.md) walks through it. The facility's tests are in
`tests/pilot_fund/`, and the fund's day in process is `tests/integration/test_pilot_fund.py`.
