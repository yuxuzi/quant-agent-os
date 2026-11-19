-- Larchmere Loan Partners (fictional): the deal model and trustee holdings in Azure SQL Database.
-- T-SQL, for the database `larchmere-clo` on the logical server `larchmere-sql.database.windows.net`.
-- Not run by the tests: the offline stand-in is SQLite (see README.md for the dialect gaps).
-- Access is by Entra ID (the Container App's managed identity is a contained user), never a password:
--   CREATE USER [larchmere-qaos] FROM EXTERNAL PROVIDER;  ALTER ROLE db_datareader ADD MEMBER [larchmere-qaos];
-- and it is granted SELECT on the views in schema [clo_read] only, never on the tables.

CREATE SCHEMA clo;
GO
CREATE SCHEMA clo_read;
GO

CREATE TABLE clo.deal (
    deal_id          NVARCHAR(24)  NOT NULL PRIMARY KEY,
    name             NVARCHAR(80)  NOT NULL,
    status           NVARCHAR(16)  NOT NULL CHECK (status IN ('REINVESTING', 'AMORTIZING', 'WAREHOUSE')),
    closing_date     DATE          NOT NULL,
    reinvestment_end DATE          NULL
);
CREATE TABLE clo.tranche (
    deal_id      NVARCHAR(24)  NOT NULL REFERENCES clo.deal (deal_id),
    tranche      NVARCHAR(4)   NOT NULL,
    seniority    TINYINT       NOT NULL,
    original_par DECIMAL(18,2) NOT NULL,
    spread_bps   INT           NULL,          -- NULL for the equity
    oc_trigger   DECIMAL(9,6)  NULL,
    ic_trigger   DECIMAL(9,6)  NULL,
    PRIMARY KEY (deal_id, tranche)
);
CREATE TABLE clo.test_limit (
    deal_id   NVARCHAR(24) NOT NULL REFERENCES clo.deal (deal_id),
    test_name NVARCHAR(24) NOT NULL,
    level     DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (deal_id, test_name)
);
CREATE TABLE clo.industry (industry_code NVARCHAR(8) NOT NULL PRIMARY KEY, name NVARCHAR(80) NOT NULL);
CREATE TABLE clo.obligor (
    obligor_id    NVARCHAR(8)  NOT NULL PRIMARY KEY,
    name          NVARCHAR(120) NOT NULL,
    industry_code NVARCHAR(8)  NOT NULL REFERENCES clo.industry (industry_code),
    country       NCHAR(2)     NOT NULL
);
CREATE TABLE clo.loan (
    loan_id          NVARCHAR(8)  NOT NULL PRIMARY KEY,
    obligor_id       NVARCHAR(8)  NOT NULL REFERENCES clo.obligor (obligor_id),
    facility_type    NVARCHAR(12) NOT NULL CHECK (facility_type IN ('FIRST_LIEN', 'SECOND_LIEN', 'BOND')),
    spread_bps       INT          NOT NULL,
    floor_bps        INT          NOT NULL,
    fixed_coupon_bps INT          NULL,       -- bonds only
    maturity_date    DATE         NOT NULL
);
CREATE TABLE clo.rating_scale (rating NVARCHAR(4) NOT NULL PRIMARY KEY, rating_factor INT NOT NULL);
CREATE TABLE clo.rating_history (
    obligor_id     NVARCHAR(8) NOT NULL REFERENCES clo.obligor (obligor_id),
    effective_date DATE        NOT NULL,
    rating         NVARCHAR(4) NOT NULL REFERENCES clo.rating_scale (rating),
    PRIMARY KEY (obligor_id, effective_date)
);
CREATE TABLE clo.holdings (                   -- the trustee's month-end holdings, loaded from curated/
    report_date  DATE          NOT NULL,
    deal_id      NVARCHAR(24)  NOT NULL REFERENCES clo.deal (deal_id),
    loan_id      NVARCHAR(8)   NOT NULL REFERENCES clo.loan (loan_id),
    par          DECIMAL(18,2) NOT NULL,
    market_price DECIMAL(9,4)  NOT NULL,
    loaded_at    DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (report_date, deal_id, loan_id)
);
CREATE TABLE clo.tranche_balance (
    deal_id NVARCHAR(24) NOT NULL, report_date DATE NOT NULL, tranche NVARCHAR(4) NOT NULL,
    balance DECIMAL(18,2) NOT NULL,
    PRIMARY KEY (deal_id, report_date, tranche)
);
CREATE TABLE clo.cash_balance (
    deal_id NVARCHAR(24) NOT NULL, report_date DATE NOT NULL,
    principal_cash DECIMAL(18,2) NOT NULL, interest_cash DECIMAL(18,2) NOT NULL,
    PRIMARY KEY (deal_id, report_date)
);
CREATE TABLE clo.equity_nav (
    deal_id NVARCHAR(24) NOT NULL, report_date DATE NOT NULL,
    nav DECIMAL(18,2) NOT NULL, monthly_return DECIMAL(18,8) NULL, distribution DECIMAL(18,2) NOT NULL,
    PRIMARY KEY (deal_id, report_date)
);
