# Four more mock funds

Kestrel Point Capital ([pilot fund](../pilot-fund/README.md)) is a multi-asset fund on its own servers.
These four funds are the other shapes a Wall Street firm commonly takes, each in a public cloud with the
database such a firm would run. Every name, number, and document is fictional and generated. Each fund
adopts the platform the way Kestrel does and records, in its `FINDINGS.md`, where the platform falls short.

| Fund | Kind | Cloud and database | Its own tools |
| --- | --- | --- | --- |
| [Larchmere Loan Partners](larchmere/README.md) | CLO manager, two deals | Azure: ADLS Gen2 lake, Azure SQL (SQL Server), Entra ID, Key Vault, Container Apps | OC and IC per class, CCC haircut, WARF, WAS, WAL, diversity, concentration limits, pre-trade what-if, equity IRR |
| [Corvane Equity Partners](corvane/README.md) | Benchmark-aware long-only equity | AWS: S3 lake, Snowflake, Cognito, Secrets Manager, ECS Fargate | Active share, tracking error, Brinson-Fachler with Carino linking, currency effect, days to liquidate, mandate check |
| [Sablecroft Alternatives](sablecroft/README.md) | Fund of hedge funds and private markets | Azure: ADLS with Delta, Azure Databricks, Entra ID, Key Vault, Container Apps | XIRR, TVPI, DPI, RVPI, Kaplan-Schoar and Long-Nickels PME, unsmoothing, Lo-adjusted Sharpe, liquidity ladder, revision bias |
| [Wrenfield Credit Opportunities](wrenfield/README.md) | Credit hedge fund | AWS: S3 lake with DuckDB, RDS PostgreSQL, Cognito, Secrets Manager, ECS | Spread duration and DTS, CS01 and DV01 ladders, jump to default, spread and default stress, bond-CDS basis, rating migration |

Each fund has the same parts: `facility/` is the fund's own code and imports nothing of the platform (a
deterministic generator writing the lake and database stand-ins and `known_answers.json`, an untyped legacy
package, research documents with one injected instruction, a mock identity provider, and an access file);
`site/` is what it writes to adopt the platform (a `DatasetStore`, its tools registered with `FirmApp`, its
server, acceptance cases, the real SQL DDL and views, and deployment descriptors that are not run). The
tests are in `tests/mock_funds/` and run offline. Cloud services are local stand-ins: a directory per bucket
or container, SQLite or DuckDB per database, a stdlib issuer of RS256 tokens shaped as Entra ID or Cognito
issues them.

## What they found together

Each fund's table marks a finding measured (a test or command shows it) or inferred. The rows below are the
generic ones that more than one fund met, with the round-2 work that answers them.

| # | Gap | Funds | Evidence | Next step |
| --- | --- | --- | --- | --- |
| 1 | Sign-in checks tokens only by RFC 7662 introspection, which Entra ID and Cognito do not offer | all four | Every token refused; each fund wrote a 70 to 159 line shim. Audience, scope names, GUID groups, app roles, machine tokens, and the audit principal all needed mapping | JWKS verification and claim mapping, opt-in (`feat/jwt-sign-in`) |
| 2 | Each fund hand-writes a SQL or lake `DatasetStore` | all four | 268 to 499 lines each, of which 80 to 330 generic; a same-day call and distribution netted in a pivot (78.25m paid-in instead of 80.33m) | A SQL connector in Quant Toolbox and a `SqlDatasetStore` (`feat/sql-connector`) |
| 3 | Evidence lists one source per column, and every expected gap is a warning | all four | One call failed at 128,036 bytes against the 100,000 limit; others reached 89,630 and 64 KB; `list_datasets` returned 25 KB for 19 datasets | Done (`feat/output-hygiene`): Corvane's currency effect 134,651 bytes to under 5,300; listing 25,387 to 10,517 |
| 4 | Datasets are numeric only | all four | Sector, rating, currency, strategy, and dates encoded as integers; trustee pass/fail results could not be served | String and date columns in table-shaped datasets |
| 5 | Acceptance cases cannot read a list, expect a refusal, or use a relative tolerance | all four | Each fund flattened its outputs for the cases and tested entitlements in pytest | Done (`feat/acceptance-cases`): list paths, `[[exact]]`, `[[refusal]]`, `relative_tolerance` |
| 6 | The run store is a SQLite file | all four | Forces one replica on Container Apps or Fargate, on Azure Files or EFS | A PostgreSQL run store |
| 7 | Entitlements are by dataset id only | all four | 14 to 16 split datasets per fund; a restricted issuer's column readable by analysts; no per-document control | A design for row, column, or tag rules (a security change: decision first) |
| 8 | One asset's missing history cuts the risk window | Sablecroft, Corvane, Wrenfield | 60 months to 42, 109 days to 29; a held defaulted bond with no loading fails the factor model | Done (`feat/output-hygiene`): an asset under 90% of the dates is left out and its weight reported; Sablecroft keeps 60 months |
| 9 | Defaults assume daily data | Larchmere, Sablecroft, Corvane | Horizons of 21 bars on monthly data; a carried-forward monthly score inflates a 21-day IC from 0.039 to 0.146 with no warning | Done (`feat/output-hygiene`): horizons by frequency; carry-forward and thin cross-section warnings |
| 10 | Daily analytics each kind of fund needs are missing | per fund | Money-weighted returns and PME (Larchmere, Sablecroft), smoothing (Sablecroft), credit risk (Larchmere, Wrenfield), benchmark-relative attribution (Corvane), financing costs in backtests (Wrenfield) | Quant Toolbox subpackages, one per family |
| 11 | Operating in a cloud | all four | `/healthz` checks nothing, logs are plain text, secrets only from the environment, no token callable for managed Postgres, no recipe for adding drivers to the image, no cloud section in the deployment guide | Done (`feat/cloud-ops`): `/readyz`, `QUANT_AGENT_OS_LOG_FORMAT=json`, `NAME_FILE` secrets, and a cloud section; the descriptors use them |
| 12 | Ingestion reads a local folder of text, Markdown, and PDF | Larchmere, Corvane, Wrenfield | A SharePoint `.docx` was skipped; S3 and Blob need a sync first; citations are relative paths | Office loaders and object-store sources |
| 13 | `qaos legacy scan` misreads module state | all four | State set by another module, plumbing offered as tools, a monthly entry point left out | Converter fixes |

## Round 1

Round 1 adopted the 1.0 candidate (`integration/1.0-r1`) at the two Azure funds. Each fund's `FINDINGS.md` has
its own "Round 1" table (`R1-L…` for Larchmere, `R1-S…` for Sablecroft). The generic rows are below.
Corvane and Wrenfield have not run round 1 yet.

| # | Gap | Funds | Evidence | Next step |
| --- | --- | --- | --- | --- |
| R1-G1 | `/readyz` reads each store's `ids()`, which both funds answer from a static list, so a dead database or warehouse still answers ready | Larchmere, Sablecroft | `/readyz` is 200 with the SQL file or the warehouse folder deleted (`test_readyz_still_says_ready...`, both funds); 503 only when the run store breaks | A check that reads a declared dataset (or a store `check()`), so the readiness probe sees the data source |
| R1-G2 | Frequency defaults to daily, has no quarterly choice, and horizons are labelled for days | Larchmere, Sablecroft | `performance-report --defaults` writes 252 for a monthly fund: ITD 33.16% against 10.02% at 12, and no warning (R1-L6). Quarterly data has no quarterly choice and shows the quarter under MTD (R1-S2, R1-S3) | Ask the frequency with no silent default; add quarterly; horizons by frequency |
| R1-G3 | Preliminary, revised, and restated figures have no vintage, and a stale last observation is not flagged | Larchmere, Sablecroft | A last quarter's first print is the figure, and `reconcile` flags three rows against the revised statement (R1-S5). A trustee's restatement turns a PASS into a FAIL with nothing to hold both (R1-L2). Nothing compares the last date with the as-of (R1-S4) | A vintage or as-of convention for datasets; a preliminary mark; an age warning on the last observation |
| R1-G4 | The AI-use report names the principal as an object id and holds no arguments | Larchmere, Sablecroft | `principal` is `a1000000-...-0002`, and the what-if's loan id and size are absent (R1-L9, R1-L10, R1-S10) | Use the sign-in's `principal_claim` (it exists; see R1-G6); the fund keeps its own pre-trade log |
| R1-G5 | An acceptance run leaves no record | Larchmere, Sablecroft | `run_acceptance` takes no run store (both funds' tests; R1-L17, R1-S11) | A run-store option for `qaos eval`, so agreed cases are in the record |
| R1-G6 | `verify = "jwks"` accepts Entra-shaped tokens with no shim (decision 0031) | Larchmere, Sablecroft | Principal `UPN@tid`; groups and roles together; audience `api://` or https as configured (R1-L15, R1-S12). Both funds still run their shims | The owners decide the sign-in switch; the shims then retire |
| R1-G7 | Deployment descriptors pin an image that predates the features they set, or pin nothing | Larchmere, Sablecroft | `quant-agent-os:0.7.0` (released 2026-11-17) predates `/readyz`, JSON logs, and `NAME_FILE`, which are Unreleased (R1-L14). Sablecroft uses `quant-agent-os:local` and `pip ... \|\| true` (R1-S17) | Pin the release that carries 1.0, once tagged; check the pin against the features in a test |
| R1-G8 | `app new --defaults` cannot choose the source, and `app create` recorded a run in the user's default home | Larchmere | The default source is `git+https://...@v0.9.0`; the spec needed a hand edit (R1-L7). `app create` wrote run `78cfd84d...` to `~/.quant-agent-os/runs.db` (R1-L8) | A source option on `app new`; print the home a record goes to |
| R1-G9 | The performance view covers one series: no money-weighted measure, no currency, no vintage | Larchmere, Sablecroft | 18 metrics and no IRR or MOIC for the equity series (R1-L16); no currency for a EUR manager (R1-S13) | Money-weighted measures and currency metadata (cross-fund rows 10 and 13) |
| R1-G10 | A reconciliation reference that names a group row as its header fails with a misleading message | Larchmere | `header_row = 4` gives "no rows of data below row 4", with 36 rows below (R1-L5) | Name the header the reader expected and the one it found |

Round 1 also met two things that work: a 2026-07-28 client gets the same results over both handshakes, and
`performance-report` reconciles a percent or two-row workbook, exiting 1 on a break (R1-L4, R1-S8).
