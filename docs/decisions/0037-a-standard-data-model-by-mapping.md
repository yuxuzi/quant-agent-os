# 0037: A standard data model, reached by mapping and adapters

- Status: proposed on 2026-11-24, from the owner's direction; builds on [0032](0032-sql-dataset-store.md)
  and [0026](0026-quant-toolbox.md); the 1.0 plan's workstream W11
- Date: 2026-11-24

## Context

Every mock fund hand-wrote its `DatasetStore`: 240 to 500 lines each, most of them reshaping its own
tables into the platform's wide datasets. The rounds found the same gaps at each: text and dates encoded as
numbers, identifiers re-keyed without history, restated figures with no vintage, one benchmark per returns
file, and 14 to 16 datasets per fund split by book only so access rules could tell them apart.

Funds already have warehouses, administrators, and vendor feeds, and the platform does not replace them.
What a fund lacks is a short, stated path from what it has to what the tools and applications read.

## Decision

**Six standard tables, defined in Quant Toolbox, all long:**

| Table | Keyed by | Holds |
| --- | --- | --- |
| `securities` | instrument, valid from and to | identifiers (ticker, CUSIP, ISIN, FIGI) with their history, asset class, currency, sector, rating, issuer |
| `prices` | instrument, date, field, source | close, adjusted close, yield, spread, and so on |
| `returns` | entity, date, as of | return, frequency, basis (gross or net), currency; an entity is a fund, book, strategy, share class, or benchmark |
| `holdings` | book, instrument, date | quantity, market value, weight |
| `measures` | entity, date, measure, model, as of | risk and analytics outputs: VaR, exposures, OAS, factor loadings |
| `reference` | as named | trading calendars, FX rates, benchmark constituents, entity relations (book to benchmark) |

- **The toolbox owns the shapes, not the data.** `quant_toolbox.data.model` gives each table's columns and
  types, a validator that names what is wrong, the long-to-wide step the tools need, and the as-of rule: the
  latest vintage at or before a date unless a vintage is asked for. Pure Python and numpy; no database code.
- **A fund maps, once.** A mapping file names, for each standard table, where it comes from and what its
  columns are called there:

  ```toml
  [returns]
  adapter = "sql"
  source = "warehouse"
  query = "select book_id, as_of_date, net_ret, 'net' as basis from perf.daily_returns"
  columns = { entity = "book_id", date = "as_of_date", value = "net_ret" }
  ```

- **Adapters fill the tables.** Built in: `files` (CSV, Parquet, Excel), and `sql` (over 0032's
  connections). A source too complicated to finish now, such as a vendor's API, an administrator's portal,
  or a trustee's workbook, gets a **stub adapter**: a generated, typed Python file with each method raising
  `NotImplementedError` and a `TODO` naming what to fill, plus a test that fails until it is filled, as
  `qaos legacy wrap` drafts do (decision 0029). The firm's engineer finishes it when the source is worth it;
  until then the rest of the mapping works.
- **The platform serves standard datasets.** A `MappedDatasetStore` reads the mapping and serves ids such as
  `returns:book=GQ` or `prices:universe=US300`, wide as the tools take them, with text and date columns from
  `securities`. A fund's own `DatasetStore` keeps working beside it.
- **Applications read the same shapes.** A template pack's settings gain a `[data]` section that points at the
  mapping or at files in the standard shapes, so the same mapping serves the AI client and the firm's
  generated applications.

**Not decided here:** access by entity rather than by dataset id. The mapping makes it possible; whether and
how is a security change for the owner.

## Order

1. The toolbox's table definitions, validators, and as-of rule, and the stub-adapter generator, before 1.0.
   They add names and remove none (decision 0035).
2. Prove the mapping and `MappedDatasetStore` at two mock funds, Corvane (Snowflake) and Sablecroft
   (Databricks), in round 3, measuring the lines each fund's store loses.
3. Ship the mapping layer when that round shows it holds; 1.1 if it is after 1.0.

## Consequences

- A fund's data work becomes a mapping file and, where needed, a finished stub, instead of a store class.
- The six tables become a public promise once released, so their columns change only under 0035's rules.
- The model will not fit everything. A fund keeps its own `DatasetStore` for what it does not cover, and the
  rounds record what did not fit.
