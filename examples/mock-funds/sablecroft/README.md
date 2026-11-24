# Sablecroft Alternatives (fictional)

An alternatives allocator on Azure, with two books: a **fund of hedge funds** (25 fictional managers across
equity long/short, macro, event driven, relative value, CTA, multi-strategy, illiquid credit, and real assets) and
a **private markets program** (12 buyout, venture, private credit, and real-estate funds, USD and EUR). All data
is fictional, deterministic for a seed, and built offline.

## Who uses it

CIO (everything); manager-research analysts, one team per strategy group; the private-markets team; risk; investor
relations (the fund's aggregate numbers only); operations (NAV reconciliation and revision checks). Their
entitlements are in `facility/access.toml`, keyed by Microsoft Entra ID security-group GUIDs.

## The estate, and its local stand-ins (nothing talks to Azure)

| Real | Stand-in here |
| --- | --- |
| ADLS Gen2 `sablecroftlake`, containers `raw` and `uc` | directories under the build output, mapped by `lake_map.json` |
| Administrator NAV statements and GP capital-account statements as files in `raw/` | CSV files, last 3 months and Q3 2025 |
| Unity Catalog `sablecroft` (`curated`, `ref`) Delta tables on ADLS | Parquet folders with a minimal `_delta_log/` |
| Databricks SQL warehouse | DuckDB (dev dependency) over those folders, running `site/sablecroft_views.sql` |
| Entra ID tenant | `facility/identity/sablecroft_entra_mock.py`: RS256 JWTs, JWKS, no introspection endpoint |
| Key Vault, Container Apps, PostgreSQL Flexible Server, Log Analytics | descriptors in `site/deploy/`, not run |
| SharePoint research export | `facility/research/` and `site/deploy/ingest-from-sharepoint.md` |

## What the platform serves

`site/sablecroft_server.py` serves the platform's toolsets plus the fund's own tools (all `sab_*`, known-answer
tested): `private_fund_returns` (XIRR, TVPI, DPI, RVPI), `private_fund_pme` (Kaplan-Schoar and Long-Nickels),
`unsmooth_returns` (simplified Getmansky-Lo-Makarov), `lo_adjusted_sharpe`, `liquidity_ladder`,
`manager_peer_ranking`, and `revision_bias`. `site/sablecroft_store.py` is the dataset store: one declarative
entry per dataset over `sablecroft.serving.*` views.

## Planted truths (`known_answers.json`)

- PM01 (Dovecote Buyout I): IRR exactly 20%, TVPI 1.32.
- IC01 and RA01 are smoothed MA(2) managers; IC01's unsmoothed volatility is 1.60x reported (population), 1.54x in the sample.
- EV02's estimates run below its finals by 14 bp a month on average (t about 5.9).
- EV04 was liquidated on 2024-06-30. Finals arrive 15, 45, or about 75 days late; the data cut is 2026-01-10.
- Three NAV reconciliation breaks in `ref.nav_recon`.

## Run it

    uv run python examples/mock-funds/sablecroft/facility/warehouse/build_sablecroft.py /tmp/sab-lake
    SAB_LAKE_ROOT=/tmp/sab-lake uv run python examples/mock-funds/sablecroft/site/sablecroft_server.py acceptance \
        examples/mock-funds/sablecroft/site/sablecroft_acceptance.toml
    uv run pytest tests/mock_funds/test_sablecroft.py -q --no-cov

Round 1 (1.0 candidate): `uv run pytest tests/mock_funds/test_sablecroft_round1.py -q --no-cov`. The round's
documents are written by `facility/warehouse/build_sablecroft_round1.py ESTATE_DIR OUT_DIR`, where
ESTATE_DIR is the output of `build_sablecroft.py`. It writes the feed as received at the cut and the
administrator's revised statement.

What did not fit the platform is in [FINDINGS.md](FINDINGS.md).
