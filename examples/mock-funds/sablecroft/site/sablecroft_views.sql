-- Sablecroft Alternatives (fictional): the serving views the platform's dataset store reads, in
-- Databricks SQL over Unity Catalog `sablecroft`. One view per dataset family, long rows
-- (date, column name, value); the store pivots them to wide datasets. Run by a catalog admin:
--   databricks sql -f sablecroft_views.sql       (against the SQL warehouse `sablecroft-serving`)
-- Grant the platform's service principal: GRANT SELECT ON SCHEMA sablecroft.serving TO `<sp>`.
-- The statements stay inside a subset a local stand-in (SQLite) can also run: no backtick quoting,
-- no Delta-only functions. Strategy codes: eq_ls 1, macro 2, event 3, relval 4, cta 5, multistrat 6,
-- illiquid_credit 7, real_assets 8 (the same list as sablecroft_tools.STRATEGIES).

CREATE SCHEMA IF NOT EXISTS sablecroft.serving;

CREATE OR REPLACE VIEW sablecroft.serving.v_ret_final AS
SELECT r.period_end, r.manager_id, m.strategy, r.return_local AS value
FROM sablecroft.curated.mgr_returns r JOIN sablecroft.ref.managers m ON m.manager_id = r.manager_id
WHERE r.vintage = 'final';

CREATE OR REPLACE VIEW sablecroft.serving.v_ret_est AS
SELECT r.period_end, r.manager_id, m.strategy, r.return_local AS value
FROM sablecroft.curated.mgr_returns r JOIN sablecroft.ref.managers m ON m.manager_id = r.manager_id
WHERE r.vintage = 'estimate';

-- The final where there is one, else the estimate: the best figure published at the data cut.
CREATE OR REPLACE VIEW sablecroft.serving.v_ret_best AS
SELECT e.period_end, e.manager_id, e.strategy, COALESCE(f.value, e.value) AS value
FROM sablecroft.serving.v_ret_est e
LEFT JOIN sablecroft.serving.v_ret_final f
  ON f.manager_id = e.manager_id AND f.period_end = e.period_end;

-- Best figure in USD: (1 + r) * fx_t / fx_(t-1) - 1 for EUR managers, unhedged.
CREATE OR REPLACE VIEW sablecroft.serving.v_ret_usd AS
SELECT b.period_end, b.manager_id, b.strategy,
  CASE WHEN m.currency = 'EUR' THEN (1 + b.value) * x1.rate / x0.rate - 1 ELSE b.value END AS value
FROM sablecroft.serving.v_ret_best b
JOIN sablecroft.ref.managers m ON m.manager_id = b.manager_id
JOIN sablecroft.curated.fx_rates x1 ON x1.period_end = b.period_end
JOIN sablecroft.curated.fx_rates x0
  ON x0.period_end = (SELECT MAX(period_end) FROM sablecroft.curated.fx_rates
                      WHERE period_end < b.period_end);

CREATE OR REPLACE VIEW sablecroft.serving.v_ret_final_q AS
SELECT r.period_end, r.manager_id, m.strategy, r.return_local AS value
FROM sablecroft.curated.mgr_returns_q r JOIN sablecroft.ref.managers m ON m.manager_id = r.manager_id;

CREATE OR REPLACE VIEW sablecroft.serving.v_fx_eurusd AS
SELECT period_end, pair, rate AS value FROM sablecroft.curated.fx_rates;

CREATE OR REPLACE VIEW sablecroft.serving.v_idx_public AS
SELECT date, index_id, level AS value FROM sablecroft.curated.idx_public;

CREATE OR REPLACE VIEW sablecroft.serving.v_fof_weights AS
SELECT period_end, manager_id,
  value_usd / SUM(value_usd) OVER (PARTITION BY period_end) AS value
FROM sablecroft.curated.fof_positions;

CREATE OR REPLACE VIEW sablecroft.serving.v_fof_fund AS
SELECT period_end, nav_usd, return AS monthly_return FROM sablecroft.curated.fof_fund;

-- Signed, in the fund's currency: calls negative, distributions positive.
CREATE OR REPLACE VIEW sablecroft.serving.v_pm_cashflows AS
SELECT flow_date, fund_id,
  SUM(CASE WHEN flow_type = 'call' THEN -amount_local ELSE amount_local END) AS value
FROM sablecroft.curated.pm_cashflows
GROUP BY flow_date, fund_id;

CREATE OR REPLACE VIEW sablecroft.serving.v_pm_nav AS
SELECT nav_date, fund_id, nav_local AS value FROM sablecroft.curated.pm_nav;

CREATE OR REPLACE VIEW sablecroft.serving.v_pm_terms AS
SELECT fund_id, vintage_year, commitment_local,
  CASE WHEN currency = 'EUR' THEN 1 ELSE 0 END AS ccy_code,
  CASE WHEN index_id = 'SCI_EU' THEN 2 ELSE 1 END AS index_code
FROM sablecroft.ref.pm_funds;

CREATE OR REPLACE VIEW sablecroft.serving.v_mgr_terms AS
SELECT m.manager_id,
  CASE m.strategy WHEN 'eq_ls' THEN 1 WHEN 'macro' THEN 2 WHEN 'event' THEN 3
    WHEN 'relval' THEN 4 WHEN 'cta' THEN 5 WHEN 'multistrat' THEN 6
    WHEN 'illiquid_credit' THEN 7 ELSE 8 END AS strategy_code,
  t.lockup_months, t.notice_days, t.redemption_freq_months, t.gate_pct,
  COALESCE(p.value_usd, 0) AS nav_usd,
  CAST(REPLACE(t.investment_date, '-', '') AS INTEGER) AS investment_yyyymmdd,
  CASE WHEN m.status = 'active' THEN 1 ELSE 0 END AS active,
  CASE WHEN m.frequency = 'Q' THEN 1 ELSE 0 END AS quarterly,
  m.nav_lag_days
FROM sablecroft.ref.managers m
JOIN sablecroft.ref.terms t ON t.manager_id = m.manager_id
LEFT JOIN sablecroft.curated.fof_positions p
  ON p.manager_id = m.manager_id
 AND p.period_end = (SELECT MAX(period_end) FROM sablecroft.curated.fof_positions);

CREATE OR REPLACE VIEW sablecroft.serving.v_ops_recon AS
SELECT period_end, manager_id, our_value_usd - admin_value_usd AS value
FROM sablecroft.ref.nav_recon;
