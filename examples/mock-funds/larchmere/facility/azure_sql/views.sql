-- The views Larchmere's server reads (site/larchmere_store.py): one per dataset family, long rows,
-- in schema clo_read, which the managed identity may SELECT. T-SQL; the SQLite stand-in is in
-- facility/warehouse/build_larchmere.py (SQLITE_VIEWS), and the store queries both as clo.v_*.

CREATE VIEW clo_read.v_loan_static AS
SELECT l.loan_id, l.obligor_id, o.industry_code, l.facility_type, l.spread_bps, l.floor_bps,
       COALESCE(l.fixed_coupon_bps, 0) AS fixed_coupon_bps,
       CASE WHEN l.fixed_coupon_bps IS NULL THEN 0 ELSE 1 END AS is_fixed,
       DATEDIFF(DAY, '1970-01-01', l.maturity_date) AS maturity_epoch_day
FROM clo.loan AS l JOIN clo.obligor AS o ON o.obligor_id = l.obligor_id;
GO
CREATE VIEW clo_read.v_holdings AS
SELECT CONVERT(CHAR(10), report_date, 23) AS report_date, deal_id, loan_id,
       CAST(par AS FLOAT) AS par, CAST(market_price AS FLOAT) AS market_price
FROM clo.holdings;
GO
-- Rating in force on each report date, by the latest history row on or before it.
CREATE VIEW clo_read.v_loan_rating_factor AS
SELECT CONVERT(CHAR(10), h.report_date, 23) AS report_date, h.loan_id, s.rating_factor
FROM (SELECT DISTINCT report_date, loan_id FROM clo.holdings) AS h
JOIN clo.loan AS l ON l.loan_id = h.loan_id
CROSS APPLY (SELECT TOP (1) r.rating FROM clo.rating_history AS r
             WHERE r.obligor_id = l.obligor_id AND r.effective_date <= h.report_date
             ORDER BY r.effective_date DESC) AS latest
JOIN clo.rating_scale AS s ON s.rating = latest.rating;
GO
CREATE VIEW clo_read.v_structure AS
SELECT t.deal_id, CONVERT(CHAR(10), b.report_date, 23) AS report_date, t.tranche,
       CAST(b.balance AS FLOAT) AS balance, t.spread_bps,
       CAST(t.oc_trigger AS FLOAT) AS oc_trigger, CAST(t.ic_trigger AS FLOAT) AS ic_trigger
FROM clo.tranche AS t JOIN clo.tranche_balance AS b ON b.deal_id = t.deal_id AND b.tranche = t.tranche;
GO
CREATE VIEW clo_read.v_cash AS
SELECT deal_id, CONVERT(CHAR(10), report_date, 23) AS report_date,
       CAST(principal_cash AS FLOAT) AS principal_cash, CAST(interest_cash AS FLOAT) AS interest_cash
FROM clo.cash_balance;
GO
CREATE VIEW clo_read.v_test_limit AS SELECT deal_id, test_name, CAST(level AS FLOAT) AS level FROM clo.test_limit;
GO
CREATE VIEW clo_read.v_equity_nav AS
SELECT deal_id, CONVERT(CHAR(10), report_date, 23) AS report_date, CAST(nav AS FLOAT) AS nav,
       CAST(monthly_return AS FLOAT) AS monthly_return, CAST(distribution AS FLOAT) AS distribution
FROM clo.equity_nav;
GO
CREATE VIEW clo_read.v_rating_history AS
SELECT obligor_id, CONVERT(CHAR(10), effective_date, 23) AS effective_date, rating FROM clo.rating_history;
GO
