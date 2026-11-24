# Larchmere Loan Partners: findings

Measured means a test in `tests/mock_funds` or a command run here shows it. Inferred means read from the
code or docs, or reasoned, and not run. The platform was not changed.

## Core

| # | Area | What happened (evidence: tool, input, error or wrong/missing output) | Severity (blocker/major/minor) | Generic platform fix suggested | Fund-specific or generic |
| --- | --- | --- | --- | --- | --- |
| 1 | Data shape | Datasets are numeric only. Loan terms had to be encoded: `obligor_num` (OB017 to 17), `industry_num`, `facility_code` (1 first lien, 2 second lien, 3 bond), and maturity as `maturity_epoch_day` (`clo_read.v_loan_static`). The trustee's PASS/FAIL results and ratings cannot be datasets at all, so the trustee's own test results are not served. Measured. | major | Allow string and date columns in a table-shaped dataset (keep numeric-only for time-series), or a typed "reference table" kind. | Generic |
| 2 | Data shape | A snapshot calculation needs six dataset references per call (`par`, `price`, `rating_factor`, `static`, `structure`, `sofr`), each read whole (37 dates by up to 200 loans). Scalars (SOFR, principal cash, triggers, limits) have no home, so `structure_<deal>` carries them as 32 columns, `limit_*` repeated on every date. Measured. | major | A named bundle of datasets that one argument can reference; a key-value "parameters" dataset kind; selecting one row (as-of) at read time. | Generic |
| 3 | Output limits | `clo_compliance_tests` on 2021-1 returned 89,630 bytes against the 100,000 default `max_output_bytes`, of which 204 warnings were "par_larch_2021_1.LN0001 has 19 missing values, passed as NaN". A loan not held on a date is correctly missing, so the warning is noise, and a few more loans would make the call fail. Measured. | major | Aggregate missing-value warnings per dataset (one line, count of columns); let `expose` mark a parameter's missing values as expected. | Generic |
| 4 | Discovery | `list_datasets` returned 25,387 bytes for 19 datasets because every column of a wide dataset is listed (200 loans). A per-date snapshot design would be 144+ datasets. Measured. | major | Summarise wide datasets (column count, first N columns, a way to search them); paginate and filter by id pattern. | Generic |
| 5 | Acceptance | A numeric case's `value` path cannot index a list or pick by key, so a tool returning `tests: [{name, value}]` cannot be asserted; I added a duplicate flat `values` dict to the output just for `larchmere_acceptance.toml`. Measured. | minor | Support `tests[name=oc_e].value` or JSONPath in `value`. | Generic |
| 6 | Acceptance | A case cannot say "this caller must be refused". Entitlement checks (analyst refused `par_*`, PM refused the other deal) live only in my pytest. Measured: the gold file model has no such field. | major | Add `expect = "denied"` or `"unknown_dataset"` to cases, so entitlements are agreed, not just numbers. | Generic |
| 7 | Acceptance | Tolerance is absolute only: a WARF of 2630.86 and an OC of 1.1214 need different tolerances, and a relative one (1e-9) is what a quant means. Measured by writing the file. | minor | `rel_tolerance`. | Generic |
| 8 | check_app | `check_app` passed for all three tools with one example each (measured), but it cannot assert a known answer or an expected refusal, and it calls each tool three times (direct, MCP, repeat), so a tool reading 7 frames pays 21 dataset builds. | minor | Optional `expect` per example; build datasets once per check. | Generic |
| 9 | FirmApp | `clo.expose` is a plain call, so the natural `@clo.expose(par=Frame, ...)` is a `call-arg` mypy error and a runtime failure; passing the markers as a `**dict` fails strict mypy ("incompatible type `**dict[str, DataMarker]`" against `convention: str`). I wrote the markers out each time. Measured. | minor | Support the decorator form; take `data={"par": Frame}` as one typed argument. | Generic |
| 10 | Calendar | `as_of="2026-10-31"` (a Saturday, the calendar month-end) fails: "the par dataset has no row for 2026-10-31; the nearest date is 2026-10-30." The fund's tool had to implement as-of selection itself. Measured. | minor | An as-of helper on `Frame` parameters ("last row on or before"), shared by all firm tools. | Generic |
| 11 | Frequency | Monthly worked: `analyze_performance` on the equity returns inferred `periods_per_year` 12 and used 36 periods (the first, empty month was dropped). Measured, and good. But `equity_cf_*` (payment dates plus the first and last report dates) was inferred as quarterly (4), which is wrong for its mixed dates. Measured. | minor | Report the inference's confidence; allow a dataset to declare its frequency. | Generic |
| 12 | Analytics | No money-weighted measures. An equity tranche's investor wants IRR, cash-on-cash, and MOIC from dated flows; `analyze_performance` is time-weighted only. I wrote `clo_equity_returns` (XIRR by bisection, actual/365). | major | XIRR, MOIC, DPI, and cash-on-cash in Quant Toolbox `performance`, with a known-answer test. | Generic (the tool wrapper is the fund's) |
| 13 | Analytics | `analyze_risk` on daily loan returns answered, but "historical_stress_note: The dates (2025-05-02 to 2026-10-30) cover none of the 12 scenario windows"; the windows are equity and rates events. There is no spread duration, CS01, or credit-migration view, and VaR is on price returns without carry. Measured (the note); the missing measures are inferred from the tool's outputs. | major | Credit risk measures (spread duration, CS01, rating-migration, loss under a default-rate and recovery path) and credit-market scenario windows. | Generic |
| 14 | Analytics | `run_backtest` on a monthly loan signal reported `forced_exits: 39`: a loan leaving the pool (repaid, sold) is a "delisting" earning the default delisting return, and `delisting_return` is a single scalar between -1 and 0, so a repayment at par cannot be expressed. Measured. | major | Per-name terminal returns (a dataset), or a non-positive-only restriction lifted; name the exit kind. | Generic |
| 15 | Analytics | Signal and backtest defaults are daily: `horizons=[1,5,21]`, `lag=1` (a bar). I had to pass `horizons=[1,3]` for monthly bars; "21 bars" on monthly data is nearly two years. Measured. | minor | Default horizons from `periods_per_year`. | Generic |
| 16 | Analytics | A loan's monthly total return needs carry (coupon) as well as price; the store gives price returns only (`loan_return_monthly_*`, a SQL `LAG`). The platform has no tool that composes total return from price and coupon datasets. Inferred from the tool list. | minor | A `total_return(price, coupon)` helper in Quant Toolbox `data`. | Generic |
| 17 | Access model | Per-deal entitlements by dataset suffix (`*_larch_2021_1`) worked: a PM of 2018-2 reading 2021-1 data got an error equal to a missing dataset's, ignoring the id (measured). Gaps: the entitlement depends on a naming convention nothing enforces (a new dataset named `par_2021_1` would be unprotected); entitlements cannot be by deal as a concept. Inferred for the convention risk. | major | Dataset tags (`deal=larch-2021-1`) that access rules match on. | Generic |
| 18 | Access model | Missing cells leak holdings: `price_<deal>` has a column per loan with NaN where not held, so it reveals the deal's book to anyone who may read it. The analysts' group had to be kept off every `*_<deal>` dataset. Measured by design (the access test shows the analyst refused), the leak itself inferred. | minor | Document it; offer a per-dataset "reveals membership" tag. | Generic |
| 19 | Access model | Tool entitlements are glob-only (`clo_*`), so one PM may call `clo_what_if` (a trade check) and the analyst may not, but the platform has no notion of "may check a trade, may not execute", and the pre-trade check has no approval or four-eyes step. Inferred. | minor | Optional per-tool approval groups. | Generic |
| 20 | Output | `FirmApp` results carry one `convention` string. The compliance result mixes units (OC and IC ratios, WAS in bps, WAL in years, diversity in points) and conventions per test; nothing in the output schema carries a unit or a definition per field. Measured by reading the schema. | minor | Field-level `units` and `definition` in output models and descriptors. | Generic |
| 21 | Output | The evidence statement for a dataclass result reads "clo_compliance_tests computed its result from par_..., ..." (`_summary` only prints a number for a plain float); the headline (which tests failed) is not in the evidence. Inferred from `registration._summary`. | minor | Let a function return a one-line headline for the evidence. | Generic |
| 22 | Missing for the category | A CLO manager's daily work is the compliance run, pre-trade checks, trustee reconciliation, and a waterfall projection. OC, IC, WARF, WAS, WAL, and the diversity score are indenture-specific, so rightly the fund's tool; the building blocks are generic (rating-factor tables, par-weighted aggregates, WAL, XIRR, a diversity-score function). The trustee's reported tests (served in the lake as `trustee_tests`) have no dataset shape (finding 1), so reconciling against them is not possible in the platform. | major | A Quant Toolbox `credit` subpackage with the building blocks; trustee-vs-recomputed reconciliation as a documented pattern. | Both |
| 23 | Missing for the category | The legacy `project_equity_cashflows` is stateful and slow (a loop over loans and months, cached in a module dict, needs `load_deal` first). `FirmApp.expose` defaults to a 10 s timeout and has no job or async pattern, so a Monte Carlo projection would be a service call. Inferred. | minor | Documented pattern or a long-running-job tool kind. | Generic |
| 24 | Pre-trade | `clo_what_if` for a loan the deal does not hold works only if the loan is in `loan_static` and `rf_all`; a new-issue loan not yet in the warehouse cannot be tested. A PM wants to pass the loan's terms inline, which breaks "data by reference". Inferred. | minor | Allow a small inline "candidate" record with a stated trust level. | Fund-specific |
| 25 | Research | The injected instruction in `commentary-loan-market-q3.md` stayed inert: it was retrievable (`find_related`), the packet's `trust` was `untrusted`, and nothing else ran. Measured. The minutes were the top hit for the failure question. | n/a (works) | None. | Generic |
| 26 | Docs | Nothing in `docs/` covers Azure (or AWS): no managed identity, Entra, Key Vault, Container Apps, or Log Analytics (grep for azure and managed identity in deployment, operations, and the Postgres store: no hits). Measured. | major | An "on a public cloud" section in `docs/deployment.md` with the matrix in the Cloud findings below. | Generic |

## `qaos legacy scan` on `facility/legacy/larchmere_clo`

Measured (output saved from the run: 10 callables, 0 expose, 4 facade, 6 service).

| Callable | Classified | What was right or wrong |
| --- | --- | --- |
| `load_book`, `all_dates` | facade | Wrong: both read the database through `db.q`, but the scan does not follow a call into another module, so the dependency on a global connection is missed. |
| `calc_warf`, `calc_oc` | service | Arguable: flagged for `print` and the module dict `last_result`, but the real obstacle (a connection held in `db.conn`) is not seen. Both are pure functions of the database once the prints and `last_result` go, so they are what the fund wants as tools. |
| `get_conn`, `q` | service | Plumbing, not tools; they should be hidden, not offered. |
| `load_deal` | service | Right (state in `_STATE`), but "returns nothing; a tool must return a result" is the real reason it cannot be a tool. |
| `project_equity_cashflows` | service | Right: state in `_CACHE` and `_STATE`, depends on `load_deal`. The slow loop is not mentioned. |
| `reconcile` | facade | Wrong: the scan reports "writes files: .to_csv()" and "prints", and still calls it a facade. Writing a file must never be a tool. |
| `failing_dates` | facade | Wrong: it calls `calc_oc` once per date, 37 queries; slow, and it needs a service or a cached result. |

The bare-name imports (`from config import ...`, `from db import q`) mean the package cannot be imported as
a package, and `config` and `db` collide with any other module of that name; the fund's server would have
to put the folder on the path (as Kestrel's does). Inferred.

## Cloud (Azure)

| # | Area | What happened (evidence) | Severity | Generic platform fix suggested | Fund-specific or generic |
| --- | --- | --- | --- | --- | --- |
| C1 | Sign-in | Entra ID has no RFC 7662 introspection endpoint. Pointing the sign-in file at Entra's own path (`/<tid>/oauth2/v2.0/introspect`, a 404) refused the token; a proxy that only adds `active: true` to Entra's claims was refused as "for another audience" (the log line). Measured. The fund has to deploy a shim (about 70 lines in `larchmere_entra_mock.py`) that verifies the RS256 signature against the JWKS and rewrites the claims. | blocker | A JWT verifier beside `IntrospectionVerifier`: `jwks_uri`, issuer, audiences, and claim names in the sign-in file; keys cached and rotated. Also needed for Cognito. | Generic |
| C2 | Sign-in | `aud` is the API's application ID URI (`api://larchmere-qaos`), or the client ID GUID for v2 tokens depending on the app registration. The sign-in file's `resource` must be an http(s) URL (`SignInError`: not an http(s) URL, measured) and is the only audience accepted, so `api://...` cannot be named. | major | `audiences = [...]` in the sign-in file, separate from the resource URL. | Generic |
| C3 | Sign-in | Entra's `scp` (space-separated, `tools.call`) is not `scope`, and its names use dots not `tools:call`; `azp` is not `client_id`. The shim translated all three. Measured through the shim. | major | Configurable `scope_claim`, scope-name map, and `client_id_claim`. | Generic |
| C4 | Sign-in | GUID groups work as group names: the access file with `[groups."4b1f..."]` allowed and refused correctly. Measured. Gaps: only one claim is mapped (`groups_claim`), so app roles (`roles`: `CLO.PM`) and groups cannot be combined; with `groups_claim = "roles"` both PMs get `["CLO.PM"]` and cannot be told apart by deal (measured); the access file is unreadable without comments; a user in over 200 groups gets no `groups` claim (an overage pointer to Graph), which the verifier cannot follow (inferred). | major | `groups_claims = ["groups", "roles"]`, an alias map GUID to name, and overage handling. | Generic |
| C5 | Sign-in | The caller's principal is `sub`/`oid` (`a1000000-...0002`, measured), so run records and audit show GUIDs, not `tomas.okafor@...`. | minor | `principal_claim = "preferred_username"`. | Generic |
| C6 | Sign-in | The token's tenant: with `tenant_claim_value = <tid>` the caller acts in the issuer's pinned tenant `local` (measured: the verified claim is `local`), so Entra's tid appears twice in the file (issuer URL, claim value) and is not in the audit trail. | minor | Record the provider's tenant (`tid`) beside the platform tenant. | Generic |
| C7 | Data (SQL) | Azure SQL Database needs pyodbc and Microsoft's ODBC Driver 18 (or mssql-python); neither is in the platform's image or an extra, and the base is `python:slim` with no `msodbcsql18`. Not added; the SQL source is modelled by SQLite (`azure_sql/README.md` lists the dialect gaps). Inferred from `deploy/Dockerfile` and pyproject. | blocker (for reading SQL Server) | A `sqlserver` extra and a documented image layer; or a SQLAlchemy-backed connector with the driver as the fund's responsibility. | Generic |
| C8 | Data (SQL) | Managed-identity login: `Authentication=ActiveDirectoryMsi` in the connection string, or an access token fetched for `https://database.windows.net/` and passed as `SQL_COPT_SS_ACCESS_TOKEN`; tokens last about an hour. The store opens a connection per dataset build, which suits that, but the platform has no credential-provider abstraction for it. Inferred. | major | A credential hook in the connector (callable returning a token or password per connect). | Generic |
| C9 | Secrets | Settings read the environment and the home's `.env` only (`secret_sources()`); Container Apps `secretRef` to Key Vault arrives as an env var, which works (modelled in `larchmere.env`, `containerapp.bicep`). There is no `*_FILE` convention or Key Vault resolution, and a rotated secret needs a new revision. Inferred. | minor | Support `NAME_FILE` and a secret-reference scheme. | Generic |
| C10 | Data (lake) | There is no object-store `DatasetStore`. I read the local stand-in with DuckDB over `read_parquet('<dir>/**/*.parquet', hive_partitioning=true)` through `lake/catalog.json`, which maps each `abfss://` URI to a directory. In Azure that needs DuckDB's `azure` extension (an `INSTALL` that downloads at run time, so not for an air-gapped image) with a managed-identity credential chain. Measured offline; the Azure part inferred. | major | An object-store/lake connector with credential providers and pinned extensions. | Generic |
| C11 | Data (lake) | `duckdb`, `pyarrow`, and `pandas` are dev/extra only: the image installs `--extra analytics --extra server --extra pdf --extra postgres` (no `parquet`), so the fund's image must add them by its own layer, against a locked, read-only environment. Inferred from `deploy/Dockerfile`. | major | Document the "your own image" layer for extra wheels; a `lake` extra. | Generic |
| C12 | Data (lake) | A production lake is Delta (`_delta_log`), and plain Parquet reads of a Delta folder return deleted and old file versions. The mock is plain Parquet and says so in `catalog.json`. Inferred. | major | Delta support in the lake connector (DuckDB `delta` extension or `deltalake`). | Generic |
| C13 | Data (lake) | A hive partition `dt=2025-03-31` reads as a DATE in DuckDB, a string in SQLite stand-in; the derived dataset needed `CAST(dt AS VARCHAR)`. Measured. | minor | The connector normalises date types to ISO text. | Generic |
| C14 | Run store | The run store is a SQLite file (`QUANT_AGENT_OS_RUNS_FILE`, a `Path`); the knowledge store takes a PostgreSQL URL, the run store does not. On Container Apps the only persistent volume is Azure Files (SMB), where SQLite locking is unreliable, and the app must be pinned to one replica (`maxReplicas: 1` in the Bicep), so no high availability and an overlapping revision during a deploy means two writers. Inferred from `config/settings.py` and the Bicep. | blocker (for a firm that needs audit on managed storage) | A PostgreSQL run store (URL setting), like the knowledge store. | Generic |
| C15 | Knowledge store | The knowledge store accepts `postgresql://...`, so Azure Database for PostgreSQL Flexible Server fits, but the password comes only as `PGPASSWORD` (static), so Entra auth to Postgres (a token that expires in about an hour) is not possible without code; `pgvector` must be allow-listed (`azure.extensions`), and the shipped `init-roles.sh` assumes a superuser container. Inferred from `postgres_store.py` and `deploy/`. | major | Token-callback support; a managed-Postgres section in `docs/deployment.md`. | Generic |
| C16 | Health | `/healthz` is the only probe (Container Apps liveness and readiness both use it in the Bicep). Inferred: it does not check that Azure SQL or the lake is reachable, so a stopped or paused database looks healthy and fails per call. A serverless Azure SQL pause adds 30 to 60 seconds to the first query, against a 10 s default tool timeout. | minor | A `/readyz` that asks a store to check itself; a retry-on-connect option. | Generic |
| C17 | Logging | Logs are plain text to stderr (`%(asctime)s %(levelname)s %(name)s %(message)s`, from `observability/logging.py`), so Log Analytics gets one string column, with no JSON fields or correlation id. OpenTelemetry is the API only (no exporter), so Application Insights needs an SDK in the fund's image. Measured by reading the code. | major | JSON log format option; an OTLP exporter setting. | Generic |
| C18 | Ingress | Container Apps terminates TLS in front of an `*.azurecontainerapps.io` host; the sign-in `resource` host and the server's host checks must agree with it, and `serve` binds loopback by default (the Dockerfile sets `LARCHMERE_HOST=0.0.0.0`). Inferred. | minor | A "behind a proxy" section: forwarded headers, allowed hosts. | Generic |
| C19 | Documents | SharePoint libraries are mostly .docx, .xlsx, .pptx, and .msg. Ingesting the export (`lake/docs`) skipped `IC-pack-2026-09.docx` with a warning (`result.skipped == ["IC-pack-2026-09.docx"]`, measured): only text, Markdown, and PDF load. | blocker (for the document corpus) | Loaders for Office formats. | Generic |
| C20 | Documents | Ingestion takes a local `Path` (`IngestService.ingest(root)`), so an operator runs `azcopy sync` from Blob to a directory, then `qaos ingest`, then restarts nothing (the server reads the new snapshot). The citation is a relative path, not the SharePoint item URL, so a cited memo does not link back. Inferred from `ingest.py`. | major | `ingest` from a blob or fsspec URI; a per-source URL template for citations. | Generic |
| C21 | Raw to curated | The platform reads curated tables only; turning the trustee's raw CSV drops into curated Parquet is an ELT job (Synapse, Data Factory, or Fabric) the fund owns. Not a gap. | n/a | None. | Fund-specific |

## Data connection

`site/larchmere_store.py` is 268 lines. Rough split (measured by line ranges):

| Concern | Lines | Generic? |
| --- | --- | --- |
| Connection (`open_sql`, `open_lake`, the two environment variables) | about 42 | Yes: a URL and a secret reference in one function; the lake part needs the catalog mapping |
| Query and mapping (`Entry`, the 19 declared entries, `HOLD`, `STRUCTURE`, `RETURNS`) | about 72 | The shape is generic (id, SQL, parameters, shape); the SQL text is the fund's |
| `DERIVED` Python (`_equity_cashflows`, two sources and a merge) | about 22 | Fund-specific; the only dataset that was not one declarative entry |
| Pivot and shape (`_csv`, `_number`, `_pivot`, the table branch) | about 24 | Yes |
| Caching (lock, cache, `_check_stamp` by file mtime) | about 32 | Yes, with a version query instead of mtime |
| Fingerprint and version | 0 (the platform's `parse_dataset` does it) | Already generic |

Dialect differences hit:
- Schema-qualified names: SQLite has none, so the file is attached as `clo` and the T-SQL views are `clo_read.v_*` with tables in `clo.*`; production adds three-part names for another database. Measured (SQLite, DuckDB); T-SQL is inferred, not run.
- Date types: SQLite returns ISO text; DuckDB returns a DATE for a hive partition (needed `CAST(dt AS VARCHAR)`); pyodbc returns `datetime.date`, so the views `CONVERT(CHAR(10), d, 23)`. DECIMAL returns `Decimal` in pyodbc, so the views `CAST(... AS FLOAT)`.
- Identifier case: SQLite and DuckDB fold case, SQL Server follows the collation (case-insensitive by default), and PostgreSQL folds to lower case. No clash here (inferred beyond the two run engines).
- Parameters: `?` worked in `sqlite3`, DuckDB, and pyodbc alike; SQLAlchemy `text()` would use `:name`, so a shared connector must own the style.
- Functions: `CONCAT` is portable (SQLite 3.44+, T-SQL); `julianday` against `DATEDIFF`, `TOP (1)` and `CROSS APPLY` against a correlated `MAX`, and `LAG ... OVER` is shared.

Would a generic SQL-backed `DatasetStore` (one query per dataset id, long rows pivoted wide) have replaced my adapter? Yes for 18 of the 19 datasets: every one but `equity_cf_*` is a single query with a shape of long or table, plus the pivot and the cache. `equity_cf_*` reads Azure SQL (NAVs) and the lake (distributions) and merges them, so it needs either a view over both (not possible across the two engines), a Python hook, or one dataset per source with the tool joining them. The generic store would also need: more than one named source per file (SQL and lake), a declared version query for caching, and NaN-for-absent semantics (which the pivot gave for free).

## Top five generic improvements

1. A JWT/JWKS verifier for sign-in (C1, C2, C3, C4, C5). Evidence: Entra tokens were refused two ways, and the fund-written shim was needed.
2. A data connector in Quant Toolbox for SQL and lake sources with a declared dataset map and a credential hook (C7, C8, C10, C12, Data connection). It replaces about 250 of the 268 lines.
3. Richer dataset shapes and calls (1, 2, 3, 4, 10): string and date columns, key-value parameters, bundles of datasets, as-of selection, aggregated missing-value warnings, and summarised `list_datasets`.
4. A managed-Postgres run store and Office-format ingest (C14, C19, C20): audit on managed storage, and the documents the fund actually has.
5. Credit and money-weighted analytics (12, 13, 14): XIRR/MOIC, spread duration and CS01, and a loan exit at par that is not a delisting.

## Round 1 (1.0 candidate, 2026-11-24)

Candidate: `integration/1.0-r1` at 5a01506 (the `round1/azure` worktree). Baseline before the round:
`tests/mock_funds -k larchmere`, 36 passed. After: 58 passed (the 36, plus 22 in
`tests/mock_funds/test_larchmere_round1.py`). Measured means a test or a command run here shows it. Inferred
means read from code, docs, or the changelog, and not run.

Adopted: a 2026-07-28 client over the Entra-shaped token (`test_a_2026_07_28_client...`), the AI-use report
for a test day, `/readyz` (200, and 503 when the run store breaks), JSON logs, `_FILE` secrets, the
descriptors checked against section 11, the performance view, and the round's friction. The fund's
descriptors (`site/deploy/`) were not changed; the round's findings below say what they need.

Round friction: the trustee's monthly report is Excel, with merged header cells and two header rows, and it
restates the 30 September 2026 OC test for Larchmere CLO 2018-2. It was written by
`facility/warehouse/build_larchmere_round1.py` (the trustee's file) and read by `site/larchmere_trustee.py`
(the fund's reader). The administrator's equity statement (`build_larchmere_round1.py`) is the reference for
`performance-report`.

| # | Where | What happened | Measured or inferred | Generic or the fund's own | Severity (blocks adoption / friction / cosmetic) | Next step |
| --- | --- | --- | --- | --- | --- | --- |
| R1-L1 | Trustee workbook | Title rows, a group header merged over two rows (Deal, Test, and Result merged down; Current report and Restated from the prior report merged across), and column names on row 5. `pd.read_excel` with its defaults takes the title as the first column name. The reader checks the layout and fails closed | Measured (`test_the_trustee_workbook_has_two_header_rows_and_merged_cells`) | The fund's own (the trustee's layout) | friction | Ask the trustee for a single-row export or a CSV drop; keep the layout check |
| R1-L2 | Trustee restatement | The trustee restates LARCH-2018-2 oc_e for 30 September 2026 to 1.12200, below its trigger of 1.1225, so a PASS in the prior report is a FAIL in the restated one. Our recomputed value is 1.167871, the trustee's original, and the gap to the restated figure is 0.0459 | Measured (`test_the_restatement_turns...`, `test_the_fund_reads_the_trustee_restatement...`) | Generic: a dataset has no vintage, so the platform cannot hold both figures (as Sablecroft R1-S5) | friction | The reader keeps the restatement (done). Platform: a vintage or as-of convention for datasets |
| R1-L3 | Serving the trustee figures | The numbers are served now: `trustee_numbers_<deal>`, a lake entry in `larchmere_store.py` (two lines), one column per test. PASS and FAIL are not served (text in a numeric dataset), and restated values are not served (no vintage) | Measured (`test_the_trustee_numbers_are_a_dataset_and_its_results_are_not`) | Generic (string columns, cross-fund row 4) | friction | String columns in table datasets, or a result column kept as a separate reference table |
| R1-L4 | Reconciliation | `performance-report` against the administrator's percent workbook (`[reference] unit = "percent"`, `sheet = "Statement"`, `header_row = 5`): 35 months compared, one break at 2025-06 (-40.00 bps, the planted value), 2026-10 only on our side, and exit status 1 | Measured (commands in the scratchpad app `larchmere-perf`) | Generic | none (works) | None |
| R1-L5 | Reference header | `header_row = 4` (the group row) fails with "no rows of data below row 4", although 36 rows lie below it | Measured (command run) | Generic | cosmetic | Name the header the reader expected and found |
| R1-L6 | Frequency default | `qaos app new performance-report --defaults` writes `periods_per_year = 252` for this monthly fund. At 252 the report shows ITD 33.16% (10.02% at 12) and 3Y n/a (10.02% at 12), with no warning in `report.md` | Measured (two reports, the same returns) | Generic | blocks adoption | Ask the frequency with no silent default, or infer it from the dates and warn when they disagree |
| R1-L7 | App spec | `--defaults` cannot set `quant_agent_os_source`. The default is `git+https://github.com/yuxuzi/quant-agent-os@v0.9.0` (`questions.yml`), so a candidate needs a spec edit | Measured (the written spec had no such key; added by hand) | Generic | friction | A source option on `app new`, or an environment default |
| R1-L8 | Run record | `qaos app create` recorded run 78cfd84dcb8f42ffae44dcba317c0eec in the default home, `/Users/yuxuz/.quant-agent-os/runs.db`, because the round had not set `QUANT_AGENT_OS_HOME` yet. Not removed; the later commands used a scratchpad home | Measured (the command's own output) | Generic | friction | Print the home that a record goes to, or do not record app creation in the user's home |
| R1-L9 | AI-use report: who | The principal is the Entra object id (a1000000-0000-4000-8000-000000000002), not the person. With `verify = "jwks"` the same caller would be recorded as UPN@tid (measured in R1-L15) | Measured (`test_the_ai_use_report...`) | Generic (C5) | friction | `principal_claim` in the sign-in file (exists), set once the owner decides R1-L15 |
| R1-L10 | AI-use report: what it leaves out | The loan id (LN0042), the par change, and the price of a `clo_what_if` are not in the report. Tool, dataset, and counts are. A pre-trade record needs its own log | Measured (the markdown does not contain them) | Generic by design | friction | The fund's pre-trade log; the report stays free of arguments |
| R1-L11 | AI-use report: trades | The report counts platform approvals. Whether an approved what-if became a trade is recorded nowhere in the platform, because no approval step exists for a trade | Inferred (the report's sections; cross-fund row 9, finding 19) | Generic | friction | Per-tool approval groups (finding 19), or the order management record |
| R1-L12 | Readiness | `/readyz` answers ready with the SQL database deleted. The datasets check reads `ids()`, which `larchmere_store.py` answers from a static list. A broken run store does give 503 and names `runs`, and does not send the error | Measured (`test_readyz_still_says_ready_when_the_sql_source_is_gone`, `test_readyz_is_503...`) | Generic (the platform's check reads `ids()`) | blocks adoption | A check that reads a declared dataset, or a store `check()`. Until then, the rotation cannot see a dead data source |
| R1-L13 | JSON logs and file secrets | One JSON object per line with time, level, logger, and message. `LARCHMERE_INTROSPECTION_SECRET_FILE` and `PGPASSWORD_FILE` resolve. Setting `NAME` and `NAME_FILE` together is refused | Measured (`test_json_logs...`, `test_the_sign_in_secret_and_the_password...`) | Generic | none (works) | None |
| R1-L14 | Image pin | `site/deploy/Dockerfile` pins `quant-agent-os:0.7.0`, released 2026-11-17. `/readyz`, JSON logs, and `NAME_FILE` are in Unreleased, so the pinned image lacks them, and the settings in `larchmere.env` would not take effect | Measured (the pin, by test; the changelog order, by grep). The effect is inferred | The fund's own (the pin), and generic (the release) | blocks adoption | Pin the release that carries 1.0, once the owner tags it. Do not deploy before then |
| R1-L15 | Sign-in mode | The descriptor runs the introspection shim, with a client secret. Section 11 says Entra needs `verify = "jwks"`. Measured: the candidate's JWKS mode accepts the same tokens with no shim: `audiences = ["api://larchmere-qaos"]`, `scp` mapped to `tools:list` and `tools:call`, groups and roles together (`["4b1f...0002", "CLO.PM"]`), principal `UPN@tid` | Measured (`test_the_platforms_jwks_sign_in_takes_the_same_tokens_with_no_shim`); the deployed file not changed | Generic (the shim is the fund's stopgap; decision 0031) | friction | The owner decides, since it changes the sign-in. After the switch the shim and its Container App retire |
| R1-L16 | Performance view (equity) | `analyze_performance` on the equity series links the view, `ui://quant-agent-os/performance.html` (`text/html;profile=mcp-app`), and the page draws 18 metrics with the warning "Left out 1 rows". It shows no IRR, MOIC, DPI, or cash-on-cash. Those are `clo_equity_returns`, which has no view | Measured (`test_the_equity_return_gets_the_view...`, `test_the_deal_cash_flow_measures...`) | Generic (cross-fund row 10) | friction | A view for the deal's flows; money-weighted measures in Quant Toolbox |
| R1-L17 | Acceptance runs | `run_acceptance` takes no run store, so the operator's acceptance run leaves no record for the AI-use report. The function is shared by both funds | Measured (the signature; Sablecroft's test asserts it) | Generic | friction | A run-store option for `qaos eval` |
| R1-L18 | Dataset frequency | `list_datasets` gives `equity_cf_larch_2018_2` `periods_per_year` 4, for dated flows that are mostly monthly | Measured (the listing) | Generic (finding 11) | friction | A dataset that declares its frequency |

What compliance would still miss, reading the AI-use report for the test day: the loan, the size, and the price of
each what-if (R1-L10); whether a what-if became a trade (R1-L11); which person the principal is, unless the
sign-in names them (R1-L9); which access file applied on the day, since the header holds platform versions only;
and the acceptance runs, which leave no record (R1-L17).

The performance view against the investment committee's need for the equity tranche: the page shows the
equity series' return statistics, and the warnings and conventions behind them. It draws no money-weighted return,
which is what an equity investor asks (R1-L16). The horizon table is not in the page either; it is in
`performance-report`, and it was wrong at the generated default (R1-L6).

## Round 2 (2026-11-24)

Baseline before the round, measured: `tests/mock_funds -k larchmere` passed in this run. Round-1 fixes verified by
their tests in this run: #98 (`[reference]` reconciliation) by `test_larchmere_round1.py`; #101 (`/readyz`, JSON
logs, `_FILE` secrets) by the same file. The readiness fix did not hold at this fund (below).

**Round-1 fixes, status:**
- Fixed in #98, verified round 2: the `[reference]` table reads the percent workbook (R1-L4), by the round-1 test file.
- Fixed in #101, verified round 2 for the probe's run-store and JSON-log parts only.
- Did not hold: R1-L12 (readiness). Measured this round: with the SQL database gone, `readiness_probe` still returns
  READY, because `ids()` returns the static `DATASETS` list (`test_r2_larchmere_readiness_still_passes_with_the_sql_source_gone`).
  The "every fund's DatasetStore gains check()" change was not made in this fund's store.
- Not verified: R1-L6 (`periods_per_year` auto). The auto default is in the platform's Unreleased changelog; no test here
  asserts the auto result for this fund, and I did not generate the report app this round.

| # | Where | What happened | Measured or inferred | Generic or the fund's own | Severity | Next step |
| --- | --- | --- | --- | --- | --- | --- |
| R2-L1 | `larchmere_store.py` `ids()` and `/readyz` | Fixed in round 2: `LarchmereDatasetStore.check()` opens `clo.db` read-only and reads the catalog, uncached. With the SQL file gone, `/readyz` answers 503 naming `datasets` (`test_readyz_is_503_naming_datasets_when_the_sql_source_is_gone`; `test_r2_larchmere_readiness_fails_with_the_sql_source_gone`). Before the fix it answered 200. | Measured (tests pass) | The fund's own store | friction | Closed; R1-L12 is now fixed in round 2. |
| R2-L2 | New CLO deal mid-month | A new deal needs a code edit: `DATASETS = _entries()` runs at import (`larchmere_store.py`), so the server must restart to see it. The entitlement group for the deal is an edit to the access file, also read at start. Not run in this round: the code path says the answer is no without restart. | Inferred (code read; pinned by `test_r2_larchmere_deal_list_is_fixed_at_import`) | Generic (no reload of a store's datasets or access file) | blocker for the mid-month goal | A reload action or a file-watched entry list in the platform, or a restart window for a deal. |
| R2-L3 | Trustee and performance reads | Not changed in this round. The round-1 trustee workbook reader is in place. | Not run | Fund's own | cosmetic | None this round |
| R2-L4 | AI-use report and the new deal | Not run; the report reads the run store, so a new deal's calls would appear with its dataset ids once served. | Inferred | Generic | cosmetic | Check after the deal is served. |

### Round 2 data model notes

| Standard table | Where it lives today | Keys and identifiers | History or vintages | Reshaping in `larchmere_store.py` |
| --- | --- | --- | --- | --- |
| Security master (loans and deals) | `clo.v_loan_static` in SQLite (`clo.db`) | `loan_id` (LN####), deal id | a static view, no history | `_entries` list, `_pivot` |
| Prices | `loan_prices` in the lake catalog | `price_date`, `loan_id` | daily, no vintages | `"long"` entries, `_pivot` |
| Returns (by deal, fund) | `loan_return_daily`, equity cash flows (`_equity_cashflows`) | date by deal | daily | `_equity_cashflows` |
| Holdings or positions | `clo.v_loan_rating_factor` by report date; par per loan | `report_date`, `loan_id` | by report date | `"long"` entry |
| Measures (what-ifs, tests) | Trustee OC test restated in the trustee workbook (round 1); served as `trustee_numbers_<deal>` | deal, test, date | restated values are a second vintage | `larchmere_store.py` two-line entry |
| Reference (calendars, rates) | `sofr` in the lake (`rate_date`, `sofr_pct`) | rate date | daily | `"long"` entry |

What a standard set would save: a standard "by loan, by report date" long table would take the rating factors and the
loan static view with no reshape. What does not fit: the trustee's restated values, which are a second vintage of the same
test, and the equity cash flows, which the fund derives.
