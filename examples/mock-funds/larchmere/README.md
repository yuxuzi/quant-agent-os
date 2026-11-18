# Larchmere Loan Partners (fictional)

A CLO manager on Azure. Two deals and a small warehouse facility, about 200 broadly syndicated loans from 150
obligors in 25 industries, 36 months of trustee reports. Everything is fictional. This is a mock fund, built
to find where Quant-Agent-OS falls short for a fund like it: see [FINDINGS.md](FINDINGS.md).

## The deals

| Deal | Status | Collateral | Notes |
| --- | --- | --- | --- |
| Larchmere CLO 2021-1 | reinvesting | about 500M | CCC bucket above 7.5% from February to May 2025, every OC test still passing |
| Larchmere CLO 2018-2 | amortizing | 338M, shrinking | **fails the Class E OC test on 2025-03-31 only**, so interest is diverted and the 2025-04-21 equity distribution is zero |

Tranches AAA, AA, A, BBB, BB, and equity. Coupons are SOFR plus a spread; payment dates are the 20th of
January, April, July, and October.

## People and what they may reach (`facility/access.toml`)

Portfolio managers per deal (their own deal's data and the CLO tools), credit analysts (market data and
research, no deal-level holdings or cash flows), structuring and operations (both deals), investor relations
(equity returns and cash flows only), compliance (everything), and operators (health only). Entra ID
security-group GUIDs are the groups; `facility/gateway/directory.json` names them.

## The estate, as local stand-ins for Azure

| Azure | Here |
| --- | --- |
| ADLS Gen2 `abfss://raw@larchmereadls...` trustee CSV drops | `lake/raw/trustee/YYYY-MM/*.csv` |
| ADLS Gen2 `abfss://curated@...` Parquet, hive-partitioned | `lake/curated/<table>/dt=.../part-0000.parquet`, plus `lake/catalog.json` |
| Azure SQL Database, schema `clo` (T-SQL in `facility/azure_sql/`) | a SQLite file with the same table and view names |
| SharePoint library "CLO Research", exported to Blob | `lake/docs/CLO Research/Shared Documents/` (eight Markdown files and one .docx) |
| Microsoft Entra ID | `facility/gateway/entra_mock.py`: RS256 v2.0 tokens, a JWKS, no introspection, and the fund's shim |
| Key Vault, Container Apps, Log Analytics | `site/deploy/` descriptors, not run in the tests |

Build it with `uv run python facility/warehouse/build_larchmere.py OUT_DIR [--seed N]`; it also writes
`known_answers.json`, the planted truths with their conventions.

## What the platform serves (`site/`)

- `larchmere_store.py`: a `DatasetStore` of 19 declared datasets over Azure SQL and the lake (DuckDB).
- `larchmere_clo.py`, `larchmere_server.py`: the fund's `clo` app, three tools, known-answer tested:
  `clo_compliance_tests` (OC and IC by class, CCC excess haircut, WARF, WAS, WAL, a simplified diversity
  score, obligor, industry, second-lien, and bond limits), `clo_what_if` (a buy or sell, before and after), and
  `clo_equity_returns` (IRR, cash-on-cash, MOIC).
- The platform's own `analyze_performance`, `analyze_risk`, `analyze_signal`, and `run_backtest`, on the equity
  tranche's monthly returns and on loan returns.
- `larchmere_acceptance.toml`: agreed cases, run as named callers.

`facility/legacy/larchmere_clo/` is the desk's old pandas code (module-level config, globals, prints, a stateful
projection); `facility/research/` holds the credit memos, the indenture excerpt, committee minutes, a
methodology summary, a market commentary (carrying an injected instruction, which stays inert), and an equity
investor report.

Tests: `tests/mock_funds/test_larchmere.py` and `test_larchmere_identity.py`.
