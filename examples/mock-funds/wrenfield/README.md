# Wrenfield Credit Opportunities (fictional)

A mock credit hedge fund, built to find where Quant-Agent-OS falls short for fixed income. Every name, price,
and number is fictional. The findings are in [FINDINGS.md](FINDINGS.md).

## The fund

Four books, about 150 bonds from 80 issuers (USD and EUR), 40 single-name CDS, four indices, and 103 business days
(11 August to 31 December 2025):

| Book | PM | Capital | What it does |
| --- | --- | --- | --- |
| `hy_ls` | `pm-hy` | 400m | High-yield cash bonds, long and short; shorts pay borrow, longs are financed in repo |
| `ig_rv` | `pm-ig` | 600m | Investment-grade bonds against five-year CDS (negative basis), hedge ratio 0.9 |
| `distressed` | `pm-distressed` | 200m | CCC and defaulted names, restructuring recoveries |
| `overlay` | `pm-overlay` | none | Index CDS (CDX-like and iTraxx-like), measured against the fund's capital |

Roles: PMs per book, credit analysts by sector (market data, no positions), risk (everything), treasury and financing
(positions, repo, borrow; no signals), compliance (restricted and wall-crossed lists, positions). Planted truths: two
defaults (Halden Retail on 12 September, recoveries 0.58 secured and 0.31 unsecured; Marlowe Offshore on 3 November,
recovery 0.42), six rating events including one rising star, a spread-momentum signal with a known rank IC, and DTS per
book on the last day. `known_answers.json` states each with its convention.

## Where it runs (modelled locally)

The facility is built as an AWS estate, with local stand-ins that keep the real names and key layouts:

- **S3 data lake**, `s3://wrenfield-data-lake/curated/<table>/dt=YYYY-MM-DD/part-0000.parquet`, with a Glue catalog
  JSON, Athena views, and one raw vendor file; analysts query it with DuckDB (`site/sql/wrenfield_duckdb_analyst.sql`).
- **RDS PostgreSQL** for positions, trades, repo, and the compliance list (`site/sql/wrenfield_oms_postgres.sql`).
  SQLite stands in offline; a Postgres path in the store runs when `WRENFIELD_TEST_POSTGRES_URL` is set.
- **Cognito** user pool with `cognito:groups`, mocked in `facility/gateway/wrenfield_cognito_idp.py` (RS256, JWKS, no
  introspection). The platform cannot sign in against it directly, so `site/wrenfield_introspection_shim.py` fronts it.
- **ECS Fargate**, Secrets Manager, CloudWatch Logs, EFS for the run store: descriptors in `site/deploy/` (not run).
- **Research** documents in `s3://wrenfield-research/credit/`, ingested by an operator (`site/deploy/ingest-research.sh`).

## Layout

- `facility/warehouse/build_wrenfield.py`: the deterministic generator (`python build_wrenfield.py ROOT [--seed N]`),
  which writes the estate and `known_answers.json`.
- `facility/legacy/wrenfield_quant/`: the desk's untyped pandas scripts, with a slow stateful hazard-curve cache.
- `facility/research/`: seven documents, one of them with an injected instruction.
- `facility/access.toml`: entitlements by Cognito group.
- `site/`: the adoption code, strictly typed: `wrenfield_store.py` (lake through DuckDB, OMS through SQL, as wide
  datasets), `wrenfield_credit.py` (seven credit tools), `wrenfield_server.py`, `wrenfield_acceptance.toml`.
- `tests/mock_funds/test_wrenfield.py` and `test_wrenfield_cloud.py`: offline, about 11 seconds together.

## Tools the fund serves (prefix `wrenfield_`)

`spread_duration_dts`, `cs01_dv01_by_tenor`, `default_stress`, `spread_shock_stress`, `bond_cds_basis`,
`issuer_sector_concentration`, `rating_migration`; each states its convention and is tested against an independent
computation or a planted value. The platform's `analyze_performance`, `analyze_risk`, `analyze_signal`, and
`run_backtest` run on the same data.

## Run it

```bash
uv run python examples/mock-funds/wrenfield/facility/warehouse/build_wrenfield.py /tmp/wf-estate
WRENFIELD_ESTATE=/tmp/wf-estate uv run python examples/mock-funds/wrenfield/site/wrenfield_server.py \
  acceptance examples/mock-funds/wrenfield/site/wrenfield_acceptance.toml
uv run pytest tests/mock_funds -q --no-cov
```
