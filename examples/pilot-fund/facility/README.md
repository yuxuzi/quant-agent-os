# Kestrel Point Capital's facility (fictional)

What a fund already has before it adopts the platform. It is written as the fund's own code: nothing here
imports `quant_agent_os`, `quant_platform`, or any platform product. Every name, number, and document is
fictional.

| Part | What it is |
| --- | --- |
| `warehouse/` | `build_warehouse.py` builds a deterministic SQLite warehouse and `known_answers.json` |
| `kestrel_quant/` | The fund's legacy Python package, pandas and module-level state, untyped on purpose |
| `research/` | Sixteen Markdown documents: strategy memos, research notes, risk committee minutes |
| `gateway/` | `idp.py`, a mock identity provider (standard library only), and its `users.json` |
| `access.toml` | Desk entitlements in the platform's access-file format (decision 0021) |

## The warehouse

```bash
uv run python examples/pilot-fund/facility/warehouse/build_warehouse.py            # warehouse/kestrel.db
uv run python examples/pilot-fund/facility/warehouse/build_warehouse.py /tmp/k.db --seed 7
```

It takes under a second and writes about 6.5 MB. The same seed gives the same table contents; the
database and `known_answers.json` are not committed. 783 business days (2023-01-02 to 2025-12-31), 40
instruments: 20 equities (12 US, 8 European), 4 government bond futures, 8 G10 FX pairs, 2 credit index
proxies, 6 commodity futures. Three desks: `eq_ls` (the equities), `macro` (bonds, FX, credit), and
`commod` (commodities).

| Table | Columns | Rows |
| --- | --- | --- |
| `ref_instruments` | instrument_id, ticker, name, asset_class, sector, region, currency, desk | 40 |
| `mkt_px_daily` | date, instrument_id, close, total_return (NULL on the first day) | 31,320 |
| `pos_eod` | date, desk, instrument_id, quantity, market_value, weight (market value / desk NAV) | 31,320 |
| `trades` | trade_id, trade_date, desk, instrument_id, side, quantity, price, notional_usd, commission_usd | 6,280 |
| `sig_daily` | date, instrument_id, signal_name (`momentum`: 40 names, `carry`: 20 non-equities, `value`: 20 equities), value | 62,640 |
| `bench_daily` | date, benchmark_id (`cash_usd`, `eq_world`, `multi_asset_60_40`), total_return | 2,346 |
| `mkt_adv` | date (month ends), instrument_id, adv_usd | 1,440 |
| `fm_factors` | factor_id, name | 5 |
| `fm_loadings` | as_of_date (month ends), instrument_id, factor_id, loading (zero loadings omitted) | 2,988 |
| `fm_factor_cov` | as_of_date, factor_id_1, factor_id_2, cov_annual | 900 |
| `fm_specific` | as_of_date, instrument_id, specific_var_annual | 1,440 |

The vendor model's factors are `equity_market`, `rates` (positive when bond prices rise), `usd` (positive
when the dollar rises), `commodity`, and `credit_spread` (positive when spreads tighten). Positions are
rebalanced every five business days at the close; between rebalances quantities are fixed, so a desk's
daily return is the sum of the previous day's weight times each instrument's `total_return`.

**Planted truths**, in `known_answers.json`: the momentum signal has a mean daily rank IC near 0.022
(t-statistic near 4), carry has none (|t| under 2), and value a small positive one among equities. They
hold for every seed we tried, not only the default. The convention is in the file: Spearman correlation
across instruments, ties sharing their average rank, between a signal on day t and `total_return` on day
t+1, averaged over days, with a naive t-statistic. The answers are computed from the same rounded values the
tables hold.

That pairing is `lag: 0` to the analysis platform, whose default is `lag: 1`: a signal on day t traded on
day t+1 and scored against day t+2's return. So `signals.at_platform_default` states each signal's mean
rank IC, IR, and Newey-West t-statistic (the platform's lag count) at that default, labelled with its
convention; momentum's is near 0.026. A client that keeps `analyze_signal`'s defaults should find exactly
these, and `tests/pilot_fund/test_facility.py` checks that it does. The file also has each desk's gross
P&L and commissions, the row counts, and the factor volatilities.

The generator uses no linear algebra (its Cholesky factor and products are written out) and sums with
`math.fsum`, so its output should not depend on the platform's BLAS or summation order. The tests still
assert only bounds on the planted truths and that the answers match the tables, never one platform's
numbers.

## The legacy package

`kestrel_quant` reads the warehouse named by `KPC_WAREHOUSE` (default `warehouse/kestrel.db`, relative to
the working directory) when it first connects, and writes reports to `KPC_OUTPUT` (default `.`). Both land in
the module-level `CONFIG` dict at import.

```bash
cd examples/pilot-fund/facility
KPC_WAREHOUSE=warehouse/kestrel.db uv run python -c \
  "from kestrel_quant import reports; reports.desk_pnl_report('macro')"
```

- Simple functions: `signals.momentum_signal`, `signals.carry_signal`, `signals.value_zscore`,
  `signals.combine`, `portfolio.vol_target_weights`, `portfolio.backtest`, `reports.drawdown_table`,
  `reports.summary`, and `reports.desk_pnl_report` (prints, and writes `pnl_<desk>.csv`).
- Its smells are deliberate, the legacy converter's real input: a global connection and a never-expiring
  query cache in `db`, `print`, untyped functions, `*args` and `**kwargs`, a file written as a side effect.
- `risk_engine.RiskEngine` is stateful: `load()` reads returns, the vendor model, and every desk's book,
  builds an EWMA covariance, and waits `CONFIG["risk"]["load_delay_s"]` (1 s) as the vendor download does.
  Then `desk_var(desk, confidence=0.99, horizon=1, method="historical" | "parametric" | "factor")`,
  `stress(desk, scenario)`, `stress_all(desk)`, `factor_decomposition(desk)`, `exposure(desk)`, and
  `refresh(as_of)` answer from memory and a cache. Scenarios are in `risk_engine/scenarios.json`. Load
  once and ask many times: it belongs in a service, not behind plain functions.

## The gateway

```bash
uv run python examples/pilot-fund/facility/gateway/idp.py --port 9400 --config examples/pilot-fund/facility/gateway/users.json
```

`POST /token` (`password` grant for a user, `client_credentials` for `kpc-batch`), `POST /introspect`
(RFC 7662, HTTP Basic), `POST /revoke` (RFC 7009), and `GET /.well-known/oauth-authorization-server`.
Tokens are JWT-shaped (HS256), so the platform's `IntrospectionVerifier` picks the provider by `iss`. In
tests, `idp.start(config, clock=...)` serves on an ephemeral port and returns `base_url`, `provider`, and
`stop()`.

`users.json` sets the issuer (null: the provider's own base URL, such as `http://127.0.0.1:9400`), the
audience `http://127.0.0.1:8000/mcp`, the tenant `kestrel` in claim `qaos_tenant`, groups in claim
`groups`, scope `tools:list tools:call`, and a one-hour lifetime (a token request may ask for less with
`expires_in`). The introspecting client is `qaos-server` with secret `kpc-introspection-fixture`. Every
password is `kpc-<user>`:

| User | Group |
| --- | --- |
| `pm-equities` | `desk-eq-ls` |
| `pm-macro` | `desk-macro` |
| `pm-commodities` | `desk-commod` |
| `risk-manager` | `risk` |
| `researcher` | `research` |
| `operator` | `operators` |
| `former-analyst` | `research`, disabled: refused a token |

A token is inactive once it expires, is revoked, or its user is disabled (`provider.disable_user(name)`).

## Entitlements

`access.toml` names the dataset ids a warehouse adapter should expose:

| Dataset id | From | Shape |
| --- | --- | --- |
| `px_returns`, `px_close` | `mkt_px_daily` | date x instrument_id |
| `bench_returns` | `bench_daily` | date x benchmark_id |
| `adv_usd` | `mkt_adv` | month end x instrument_id |
| `fm_loadings`, `fm_factor_cov`, `fm_specific` | the vendor model | the adapter's choice |
| `signals_momentum`, `signals_carry`, `signals_value` | `sig_daily` | date x instrument_id |
| `positions_<desk>`, `weights_<desk>` | `pos_eod` market_value, weight | date x instrument_id |
| `desk_returns_<desk>` | `pos_eod` and `mkt_px_daily` | date x one column |

Each desk's PM sees market data, signals, and its own desk; `risk` sees every dataset; `research` sees
market data and signals, no positions; `operators` see no data. Tool names beyond the platform's own
(`kpc_*`, `risk_*`, `run_backtest`) are placeholders for the fund's `site/` code.
