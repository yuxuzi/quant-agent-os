# Azure SQL Database (modelled with SQLite offline)

`schema.sql` and `views.sql` are the T-SQL Larchmere's database holds; nothing here runs in the tests.
The tests use a SQLite file built by `../warehouse/build_larchmere.py`, with the same table and view names.

Dialect gaps between the two, all handled in one place (`SQLITE_VIEWS` in the builder, and the store's
`ATTACH ... AS clo`):

| T-SQL | SQLite stand-in |
| --- | --- |
| schemas `clo`, `clo_read` | one attached file named `clo`; views are `clo.v_*` in both, so the store's SQL is identical |
| `DATETIME2`, `DATE`, `DECIMAL(18,2)`, `NVARCHAR` | `TEXT` ISO dates, `REAL`, `TEXT`; no precision or collation |
| `DATEDIFF(DAY, '1970-01-01', d)` | `julianday(d) - 2440587.5` |
| `CROSS APPLY (SELECT TOP (1) ...)` | a correlated `MAX(effective_date)` subquery |
| `CONVERT(CHAR(10), d, 23)` (ISO date text) | dates are already ISO text |
| `?` parameters (pyodbc) | `?` parameters (sqlite3): the same |
| `CHECK`, `REFERENCES`, `SYSUTCDATETIME()` default | foreign keys not enforced; no `loaded_at` |
| permissions (`GRANT SELECT ON SCHEMA::clo_read`) | none; the file is opened read-only |

Not modelled: the Entra ID token login (`Authentication=ActiveDirectoryMsi`), TLS, firewall rules, and the
serverless pause that adds 30 to 60 seconds to the first query after idle.
