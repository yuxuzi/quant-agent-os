# 0032: A firm's SQL database is read through one declared store

- Status: accepted; builds on [0020](0020-a-firms-own-server.md), [0021](0021-per-caller-access.md),
  [0026](0026-quant-toolbox.md), and [0027](0027-quant-toolbox-package.md)
- Date: 2026-11-18

## Context

Four mock funds each wrote a `DatasetStore` over their warehouse (SQL Server, Databricks, RDS PostgreSQL,
Snowflake, and a lake read with DuckDB). Between 80 and 330 lines of each were the same: connection setup with a
secret from the environment, a parameter style per driver (`?`, `:name`, `%(name)s`), `Decimal` and date
cells, identifier case, NULL as NaN, the pivot of long rows to a wide table, and a cache with a change stamp.
One of them netted a same-day call and a distribution in a pivot (78.25m against 80.33m) with no error.
Managed databases also want short-lived tokens as passwords, which a connection made once at start-up
outlives. The owner asked for standard connection and fetch tools wrapping SQLAlchemy, if they reduce repeated code.

## Decision

- **A SQL connection lives in Quant Toolbox**, as `quant_toolbox.data.sql` and the `sql` extra
  (SQLAlchemy 2, loaded in the function that needs it, so only numpy stays required). `connect` makes an
  engine with a credential callable run for each new connection and a best-effort read-only session;
  `fetch_rows` runs `text()` SQL with `:name` binds on any driver and normalises cells; `pivot_long` pivots
  and refuses duplicate `(index, column)` pairs unless told `sum` or `last`; `SqlDataset` declares a
  dataset as data. The toolbox imports nothing of the platform, and the database's driver is the firm's.
- **The store is in the platform server.** `quant_platform.SqlDatasetStore` takes named engines, a list of
  `SqlDataset`, optional derived datasets, and a stamp per source (a version SQL, a time to live, or a
  callable). It writes the shaped rows as CSV and parses them with the platform's own parser, so a dataset has
  the checks of `qaos data add`, and its fingerprint is that of a file with the same cells. It implements
  `DatasetStore`, so `GuardedDatasetStore` applies and a refused dataset looks missing (0021). Core
  does not import the toolbox, and importing `quant_platform` needs no extra: the class loads on first use.
- **Derived datasets are marked.** A dataset no SQL can build is a Python function in `derived=`, so a reader
  sees where fund logic lives. There is no query language of ours: the SQL is the firm's, in its dialect.
- **Read-only is not a sandbox.** It sets what SQLite, PostgreSQL (psycopg), and a DuckDB file allow, and
  warns for any other dialect (finding F-6). Reads stay reads; the store is not an agent tool, and an operator
  chooses the datasets in code (decision 0020).
- **Supply chain.** `sqlalchemy` is pinned to releases at least two weeks old (2026-11-03, so the lock holds
  2.1.6, not 2.1.9 of 2026-11-16). `duckdb` and `duckdb-engine` are in the development group only.

## Alternatives considered

- **Each fund keeps its own store.** The mock funds show the cost: hundreds of lines each, and the same
  netting bug waiting in each pivot.
- **The raw DB-API per dialect.** Five parameter styles and five type behaviours are what SQLAlchemy already
  hides; wrapping the drivers ourselves would repeat that work.
- **A generic table, or a semantic layer, in YAML.** It would be a language to learn and to keep correct
  across dialects. SQL strings with a shape cover what the funds wrote.
- **Put the store in the toolbox.** The toolbox must not know the platform's `Dataset` or its parser.

## Consequences

- Rewriting Wrenfield's 499-line store on `SqlDatasetStore` took 297 lines (a measurement, not shipped),
  most of them the fund's SQL and its three derived datasets; all 44 datasets kept the original's
  fingerprints.
- Each fund still chooses its driver, its stamp, and its SQL, and tests against its own database. A source
  with no stamp is read again after 60 seconds, not never.
- A pyodbc-style driver takes its token through `connect_args`, not `credential`; the docs say so.
