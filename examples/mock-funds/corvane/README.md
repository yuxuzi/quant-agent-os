# Corvane Equity Partners (fictional)

A benchmark-aware, long-only equity manager: the most common institutional equity shape. Every name,
number, and document here is fictional and generated. The point is to adopt the platform at a fund of
this kind and record where it falls short: see [FINDINGS.md](FINDINGS.md).

## The fund

| Strategy | Benchmark | Accounts |
| --- | --- | --- |
| US Large Cap Core (`LCC`, 70 holdings) | `BM_US300`, a cap-weighted 300-name US benchmark | composite `LCC`; `SMA_PEN_A` (Aldergate pension, 7% position cap); `SMA_FND_B` (Brightwater foundation, no tobacco) |
| Global Quality (`GQ`, 45 holdings) | `BM_GLOBAL200`, 100 US and 100 non-US names in USD, EUR, GBP, JPY | composite `GQ`; `SMA_SWF_C` (Cantor sovereign fund, no tobacco, 5% cap) |

401 fictional securities (300 US, 100 abroad, and one spin-off) with GICS-style labels (fictional),
110 business days from 2026-01-02. The estate holds daily prices (raw close, total return in local
currency, volume), FX, monthly benchmark constituents, daily holdings and trades per account, corporate
actions (a 2-for-1 split on 2026-02-18, a tobacco spin-off on 2026-03-12, a cash acquisition delisting on
2026-04-22), and monthly vendor factor scores (quality, value, momentum, low volatility). A JP market
holiday calendar leaves gaps. Dividends are not modelled: a total return is a price return.

People and roles (Cognito groups, `facility/access.toml`): `pm-us-core`, `pm-global-quality`,
`analyst-industrials` (analysts by sector), `risk`, `compliance`, `client-service-aldergate` (performance
of one client's account only), `operators`.

## Where the data lives (AWS and Snowflake, modelled locally)

| Real | Stand-in here |
| --- | --- |
| S3 `raw/` vendor files, a Glue job, `curated/<table>/dt=YYYY-MM-DD/part-0000.parquet`, a Glue catalog | `facility/warehouse/build_corvane.py` writes the same layout under a directory per bucket, with `s3_map.json` saying which `s3://` bucket is which directory |
| Snowflake `CORVANE.CURATED` and `CORVANE.REF` (holdings, trades, benchmark, accounts), external tables over the S3 `curated/` prefix, a reader role per team | Parquet files a `COPY INTO` would load, loaded into an in-memory DuckDB with the same schema and table names; `site/sql/corvane_snowflake_setup.sql` is the real DDL (never run) |
| Serving views in Snowflake SQL | `site/sql/corvane_views.sql`, the portable subset that both Snowflake and DuckDB run, so the offline tests run the production SQL |
| Cognito user pool (RS256, JWKS, `cognito:groups`, no introspection) | `facility/gateway/cognito_idp.py` |
| Secrets Manager injected by ECS, ECS Fargate behind an ALB, EFS for the run store, RDS PostgreSQL for the knowledge store | `site/deploy/` descriptors, never run |
| Research documents in an S3 prefix | `s3/corvane-research/research/`; an operator syncs and runs `qaos ingest` |

## What is served

`site/corvane_server.py` serves the platform's analytics (`analyze_performance`, `analyze_risk`,
`analyze_signal`, `run_backtest`), research search, and eight fund tools in `site/corvane_tools.py`:
active share, ex-post and ex-ante tracking error, Brinson-Fachler attribution (single period, and
multi-period linked by Carino), currency effect for Global Quality in USD, days to liquidate from ADV, and
a mandate restriction check. Datasets come from `site/corvane_store.py`: one declarative SQL entry per
dataset id. `site/corvane_acceptance.toml` holds the agreed cases.

```
uv run python examples/mock-funds/corvane/facility/warehouse/build_corvane.py ./corvane-estate
CORVANE_SQL_URL=estate://$PWD/corvane-estate uv run python examples/mock-funds/corvane/site/corvane_server.py
uv run pytest tests/mock_funds/test_corvane.py -q --no-cov
```

## Planted truths (`known_answers.json`, each with its convention)

Active share and tracking error on 2026-05-15; an LCC attribution for May and a monthly Carino-linked
one, where Industrials selection is the largest selection effect; a currency effect for Global Quality;
the days-to-liquidate profile; the four days two no-tobacco accounts held the spin-off; and a positive
rank IC for quality (about 0.039) with none for the other three factors. Each is computed by code that
shares nothing with the tools, and the tools reproduce it to 1e-12.
