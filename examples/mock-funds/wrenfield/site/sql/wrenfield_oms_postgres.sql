-- Wrenfield Credit Opportunities (fictional): the order-management database on Amazon RDS
-- PostgreSQL 16, schema `oms`. The offline tests use SQLite with the same table and column names
-- (facility/warehouse/build_wrenfield.py); the store's SQL is the common subset of the two.
CREATE SCHEMA IF NOT EXISTS oms;
SET search_path = oms;

CREATE TABLE ref_books (
  book text PRIMARY KEY, pm text NOT NULL, capital_usd numeric(18, 2)
);
CREATE TABLE ref_issuer_master (
  issuer_id text PRIMARY KEY, name text NOT NULL, sector text NOT NULL,
  has_cds boolean NOT NULL, book text REFERENCES ref_books (book)
);
CREATE TABLE pos_eod (
  date date NOT NULL, book text NOT NULL REFERENCES ref_books (book), bond_id text NOT NULL,
  face numeric(18, 2) NOT NULL, mv_usd numeric(18, 2) NOT NULL,
  PRIMARY KEY (date, book, bond_id)
) PARTITION BY RANGE (date);
CREATE TABLE pos_eod_2025h2 PARTITION OF pos_eod FOR VALUES FROM ('2025-07-01') TO ('2026-01-01');
CREATE TABLE pos_cds (
  date date NOT NULL, book text NOT NULL, name_id text NOT NULL, tenor text NOT NULL,
  protection_notional_usd numeric(18, 2) NOT NULL, PRIMARY KEY (date, book, name_id, tenor)
);
CREATE TABLE pos_index (
  date date NOT NULL, book text NOT NULL, index_id text NOT NULL,
  protection_notional_usd numeric(18, 2) NOT NULL, PRIMARY KEY (date, book, index_id)
);
CREATE TABLE trd_trades (
  trade_id bigint PRIMARY KEY, trade_date date NOT NULL, book text NOT NULL,
  instrument_type text NOT NULL, instrument_id text NOT NULL, side text NOT NULL,
  quantity numeric(18, 2) NOT NULL, price numeric(12, 4)
);
CREATE TABLE fin_repo_rates (date date PRIMARY KEY, rate_pct numeric(8, 4) NOT NULL);
CREATE TABLE fin_borrow_fees (bond_id text PRIMARY KEY, fee_bp numeric(8, 1) NOT NULL);
CREATE TABLE pnl_daily (
  date date NOT NULL, book text NOT NULL, gross_pnl_usd numeric(18, 2) NOT NULL,
  financing_cost_usd numeric(18, 2) NOT NULL, net_pnl_usd numeric(18, 2) NOT NULL,
  PRIMARY KEY (date, book)
);
CREATE TABLE cmp_restricted (
  issuer_id text PRIMARY KEY, status text NOT NULL CHECK (status IN ('restricted', 'wall_crossed')),
  since date NOT NULL
);

-- Views the risk and treasury teams query from their SQL clients.
CREATE VIEW v_book_exposure AS
SELECT p.date, p.book, sum(p.mv_usd) FILTER (WHERE p.mv_usd > 0) AS long_mv_usd,
       sum(p.mv_usd) FILTER (WHERE p.mv_usd < 0) AS short_mv_usd, sum(p.mv_usd) AS net_mv_usd,
       sum(p.mv_usd) / b.capital_usd AS net_over_capital
FROM pos_eod p JOIN ref_books b USING (book) GROUP BY p.date, p.book, b.capital_usd;
CREATE VIEW v_financing_cost AS
SELECT p.date, p.book,
       sum(greatest(p.mv_usd, 0)) * 0.9 * (r.rate_pct + 0.35) / 100 / 360 AS repo_cost_usd,
       sum(-least(p.mv_usd, 0) * coalesce(f.fee_bp, 0)) / 1e4 / 360 AS borrow_cost_usd
FROM pos_eod p JOIN fin_repo_rates r USING (date) LEFT JOIN fin_borrow_fees f USING (bond_id)
GROUP BY p.date, p.book, r.rate_pct;

-- Roles: the server reads, ingestion and the OMS writers do not share a role with it. With IAM
-- database authentication on, the server connects with a token for qaos_reader instead of a
-- password (`aws rds generate-db-auth-token`, valid 15 minutes).
CREATE ROLE qaos_reader LOGIN;
GRANT rds_iam TO qaos_reader;
GRANT USAGE ON SCHEMA oms TO qaos_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA oms TO qaos_reader;
ALTER ROLE qaos_reader SET default_transaction_read_only = on;
-- cmp_restricted is compliance's: the server's role has no business reading it.
REVOKE SELECT ON cmp_restricted FROM qaos_reader;
