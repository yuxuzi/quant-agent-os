# Kestrel Point Capital: findings

What the mock fund's rounds found, with the evidence for each row. The 0.8.0 pilot's findings are in
[part 5 of the plan](../../docs/plans/proposals-0.8.md#5-what-the-pilot-found), and the round plan is part 3
of the 1.0 plan. Each row says whether it was measured or inferred, and whether it is generic or Kestrel's own.

## Round 1 (1.0 candidate, 2026-11-24)

The candidate is `integration/1.0-r1` at `5a01506`, worked on branch `round1/kestrel`. The platform reports
itself as 0.9.0 and Quant Toolbox as 0.8.0.

**Baseline, before any change** (the candidate with Kestrel as shipped in 0.8.0):

- `uv run pytest tests/pilot_fund tests/integration/test_pilot_fund.py`: 26 passed in 24.5 s.
- `bash examples/pilot-fund/drill.sh`: passed. Phase seconds: 1 build the wheels 0.1; 2 install 1.8; 3 warehouse 1.6;
  4 init and doctor 0.4; 5 ingest 0.2; 6 legacy draft 9.8; 7 start the services 16.8; 8 the fund's day 0.9; 9 agreed
  cases 1.6; 10 who called what 0.2. Total 33.4 (34.7 wall).

**After Round 1's adoption** (the same drill, with the new checks):

- `bash examples/pilot-fund/drill.sh`: passed, twice. The final run's phase seconds: 1 build the wheels 0.1; 2 install
  1.2; 3 warehouse 1.6; 4 init and doctor 0.4; 5 ingest 0.2; 6 legacy draft 9.5; 7 start the services 11.3; 8 the fund's
  day 1.1; 9 agreed cases 1.7; 10 who called what 0.4. Total 27.5 (28.4 wall). The earlier run gave 27.9. Phase 7 has
  varied by 5 s or more between runs (16.8 at baseline), which is the variance to expect.
- The drill's own count: 22 checks ok, 0 failed.

**Adopted with no friction:** a 2026-07-28 client gets the same tools and the same refusal text as a 2025-11-25
client (`test_a_2026_07_28_client_gets_the_fund_tools_and_the_same_refusal`, and the drill's `pm-equities` check);
`/readyz` answers `{"status": "ready"}` without a credential, and `503` with `failed: ["runs"]` once the run store is
closed (`test_readiness_answers_ready_then_names_the_run_store_once_it_is_closed`); JSON logs carry `time`, `level`,
`logger`, and `message`; the risk service reads its caller secret from `KPC_RISK_CALLER_SECRET_FILE`, refuses a call
with no assertion (401), a forged or another tool's assertion (401), and a caller outside the risk group (403)
(`test_the_risk_service_checks_every_caller_with_the_secret_its_file_holds`).

| # | Where | What happened | Measured or inferred | Generic or Kestrel's own | Severity | Next step |
| --- | --- | --- | --- | --- | --- | --- |
| R1-K1 | `qaos app new`, quant-apps performance-report | `app new performance-report kestrel-perf --defaults --yes` would install the released 0.9.0 from GitHub: the default source is `git+https://github.com/yuxuzi/quant-agent-os@v0.9.0` (`src/quant_app_factory/domain/spec.py`), and `app new` has no option for a local source. The candidate needed a spec written with `--defaults --out`, a `quant_agent_os_source` line added by hand, then `app create`. | Measured: the written spec had no source line; with the worktree as source, `app create` ran `uv sync` and ruff, format, mypy, and pytest, all ok (`gen/create.log`). | Generic | friction | A source option on `app new`, or a source read from the factory settings, so a release candidate can use the one-command start. |
| R1-K2 | `qaos app create`, run record | My candidate run wrote run `93fc95fb87674ab0aac00a9d84b8f40d` (`create_app`) to the default home, `/Users/yuxuz/.quant-agent-os/runs.db`, because I did not set `QUANT_AGENT_OS_HOME`. The command prints the path. | Measured: `qaos runs show 93fc95fb87674ab0aac00a9d84b8f40d` lists it. | Generic (the default home) | cosmetic | Set `QUANT_AGENT_OS_HOME` in round commands. The owner may prune this one run. |
| R1-K3 | performance-report provenance (`results.json`) | The report records the returns file's fingerprint and source. It does not record the reference file, its fingerprint, the tolerance, or the reconciliation result, so a report reconciled to an administrator's file reads the same as one that was not. | Measured: `results.json` keys are `as_of`, `code_version`, `created`, `data_fingerprint`, `source`; nothing in the report folder mentions the reference or reconcile. | Generic | friction | Record the reference's fingerprint and the reconcile outcome in the report's provenance (a pack change). |
| R1-K4 | Restatement after the report went out (round friction ii) | The administrator restated 2025-12 by +9 bps after a report was sent. `reconcile` exits 1 with one break, 2025-12 at -9.00 bps, over 36 months. The sent report's `results.json` is unchanged, and a new report has the same `data_fingerprint`. Nothing tells a reader that the reference moved. The fund must re-run reconcile by hand and decide which figure is right. | Measured: `reconcile-restated-2025-12.txt`, `reports-sent/before.sha` (sha256 OK), and `reports-after` with the same fingerprint, in the scratchpad `r1-kestrel` folder. | Generic | friction | A restatement step in the runbook, and R1-K3's reference fingerprint so a sent report shows which file it was reconciled to. |
| R1-K5 | Warehouse desk returns are gross | `desk_returns_<desk>` is gross, commissions not subtracted (`site/warehouse_store.py`). The equity desk's commissions total $4,095,244.93, which matches the gap between the legacy gross and net P&L (303,740,818.44 less 299,645,574) to within a dollar. The monthly drag is 1.5 to 3.8 bps, median 2.1. An administrator's file net of commissions breaks in all 36 months at the default 1 bp tolerance. | Measured from `trades.commission_usd` and `pos_eod`. NAV is estimated as the median of market value over weight; the total matches the legacy P&L. | Kestrel's own (the data's basis) | blocks adoption (for reconciling to a net file) | Kestrel decides: add a net dataset in its adapter from the trades, or obtain the administrator's figures on the gross basis. Either way, state `basis` per file. |
| R1-K6 | AI-use report: what compliance still misses | `qaos runs report` names each user who called a tool, their refused dataset reads (`desk_returns_macro`, `positions_macro`), and the standing approval (`risk_desk_var`, 1). It does not show: (a) requests that never reach a tool, such as no credential or a disabled user's new token, which get 401 and no row; (b) when a call happened, since it gives period totals only; (c) the groups a user held that day, since the run record keeps the principal and tenant only; (d) which desk a refused P&L call asked for, since the row shows `positions_macro`, not `macro`; (e) the AI client; (f) who gave the standing approval. A user who only lists tools, such as the drill's operator, makes no run and is not named. | Measured for (a), (b), (d), and the operator case (the test's row count is 2 after three attempts). Inferred for (c), (e), and (f) from the `RunRecord` fields. | Generic | friction | Keep the identity provider's sign-in log as the source for attempts. Ask the owner whether groups and the client belong on the run record; that is a data decision, not made here. |
| R1-K7 | A refused desk counts as an error as well | `kpc_desk_pnl` for a desk the PM may not see raises `DeskRefusedError`. The run records the call as `error`, and the refused dataset as refused, so one call counts under both Refused and Errors. In the drill, pm-equities has 7 calls: Refused 5 and Errors 5. The researcher's denied P&L counts as refused only. | Measured: the drill's phase 10 report, and the probe report of the same day. | Kestrel's facade raises; the status mapping is generic | friction | Decision D3 (one output schema for refusals): a refusal should read `denied`, not `error`. |
| R1-K8 | The performance view: identity and dates | The page's heading is the dataset id and column, `desk_returns_eq_ls.gross_return`, not the desk. The output has 782 periods and 252 a year, but no start or end date and no basis, and Kestrel's `basis = "gross"` setting is not passed to the tool. The page draws no chart, drawdown, or monthly table; the conventions sit in a collapsed block and in hover text. | Measured: the analyze_performance output for `desk_returns_eq_ls`; `src/quant_analytics/views/performance.html` sets the heading to `data.series_id`. | Generic (the view); the names are Kestrel's dataset ids | friction | Let a caller title the result and state its basis; add the first and last date. The PM's first view (equity curve, drawdown) is a view decision for the owner. |
| R1-K9 | The view's VaR sign and risk-free rate | `value_at_risk` reads -0.71% (the 5th percentile of returns), while `risk_desk_var` gives a positive USD loss for a desk. `sharpe_ratio` of 2.36 uses `risk_free` 0.0, the tool's default, and the output does not say so. | Measured: the probe output; `risk_free` defaults to 0.0 in `src/quant_analytics/tools.py`; `PerformanceOutput` has no rate field. | Generic (analytics), with Kestrel having two VaR signs on one desk | friction | Put the rate used in the output, and align the sign or name the difference on the page. |
| R1-K10 | A disabled PM's token, on introspection | The disabled user's token, which the server had already verified, kept working for 61.7 s (polled every 2 s). A token the server had not verified was refused at once (401), and the provider refused a new token (400). The bound is `CACHE_SECONDS = 60` in `src/quant_platform/identity/oauth.py`. | Measured: the 61.7 s probe; `test_a_pm_who_leaves_mid_day_keeps_a_cached_token_for_a_minute_and_a_new_one_is_refused`. | Generic (oauth) | friction | The owner decides whether one minute is acceptable, and the deployment guide should state it. No change made. |
| R1-K11 | A disabled PM's token, on JWKS (Entra or Cognito) | JWT verification checks the signature, `exp`, `iss`, and the audience (`src/quant_platform/identity/jwks.py`). There is no revocation list and no introspection, so a disabled user's unexpired token passes until its `exp`. | Inferred from the code. Not measured: no JWKS provider is in the fixtures. | Generic | friction | The owner sets the revocation rule for JWKS sign-in: a shorter `exp`, a deny list, or introspection where the provider has it. Not changed here. |
| R1-K12 | The mock identity provider cannot disable a user while it runs | `disable_user` exists only in Python. The HTTP surface has `/token`, `/introspect`, `/revoke`, and the metadata, and `users.json`'s `disabled` applies at start. So the drill covers a user disabled at start (`former-analyst`), not a mid-day disable. | Measured: the routes in `facility/gateway/idp.py`; the friction test disables in process. | Kestrel's own (the mock) | friction | Add an operator route to the mock, or have the drill restart the gateway after an edit to `users.json`. |
| R1-K13 | Own-desk VaR for a PM | The risk service now checks the caller, so it could scope a PM to their own desk. `access.toml` still gives `risk_*` to the risk group alone, so pm-macro's own-desk VaR is denied. P7's next step is still open. | Measured: drill phase 8, "the risk service is for the risk team only: denied". | Kestrel's own | friction | Kestrel decides: scope `risk_desk_var` by `desk` for the PM groups in the service, then grant it in `access.toml`. |
| R1-K14 | Caller forwarding moved the draft's set-up and the drill's numbers | `check_app` on a forwarding tool needs a `caller`. The drill's probe of the risk service was a `POST` to `risk_desks`, which now answers 401, so it probes `openapi.json`. The drill's legacy-draft diff moved from 676 to 754 lines with the caller edits. | Measured: `drill-baseline.log` (676) and `drill-after.log` (754). | Generic (forwarding) and Kestrel's own (the edits) | cosmetic | Stop quoting the diff's line count in the runbook; count the decisions changed instead. |
| R1-K15 | `qaos legacy wrap` counts disagree | The wrap prints "114 open decisions"; the drill's grep counts 107 TODO lines; the runbook says 110 (B6). | Measured: `drill-baseline.log`, the wrap line and the drill's grep line. | Generic | cosmetic | One count, from one definition, in the output and the runbook. |
| R1-K16 | `qaos doctor` sign-in line | It reads "tenant local, tenant claim qaos_tenant", while the tokens name `kestrel`, which `tenant_claim_value` maps to the local tenant. The line is correct, but it reads as a mismatch. | Measured: `drill-baseline.log` phase 4; `site/sign-in.toml`. | Generic (wording) | cosmetic | Print the claim value beside the tenant. |
| R1-K17 | The runbook does not describe this candidate | `docs/pilot-runbook.md` does not mention `/readyz`, the JSON log setting, the caller secret file, `qaos runs report`, or the performance-report reconciliation. | Measured: a grep of the runbook finds none of them. | Kestrel's own (the docs) | friction | The docs owner updates the runbook. Docs are out of this round's scope. |
| R1-K18 | `/readyz` logs a warning on every failed probe | A failing `/readyz` writes one WARNING line on each probe, so a load balancer that probes every few seconds writes a warning each time. | Measured: one WARNING per failing probe (the readiness test). The probe rate's effect is inferred. | Generic | cosmetic | Log on a change of state, or rate-limit the warning. |

**Could not run:**

- A real MCP Apps host, to see the performance page drawn. The page was read from source and its script was checked
  against the result's fields. The CHANGELOG says no real client has shown it either.
- A cold uv cache install. The drill's install phase ran with a warm cache, as in the 0.8.0 pilot.
- JWKS sign-in (R1-K11). No JWKS provider is available to the fixtures.
- Linux CI. Everything ran on macOS.
- The one-command `qaos app new ... --defaults --yes` as written. It would install 0.9.0 from GitHub (R1-K1), so the
  candidate was used through an edited spec, the route Lab 3 takes.

**How the reconciliation was run** (scratchpad folder `r1-kestrel/gen/kestrel-perf`): `admin_fixture.py` wrote the
daily returns and the administrator's workbook (percent, title rows, header on row 5); the app's `config.toml` set
`returns`, `basis`, and `[reference]` (`file`, `unit = "percent"`, `sheet`, `header_row = 5`); then
`kestrel-perf reconcile --tolerance-bps 1` gave one break at 2025-06, -9.00 bps, over 36 months (exit 1). With a clean
workbook it reported "Clean: 36 months agree within 1 bps" (exit 0). With 2025-12 restated it reported the same
shape at 2025-12 (exit 1).

**Pilot tests after Round 1:** `uv run pytest tests/pilot_fund tests/integration/test_pilot_fund.py`: 37 passed in 30.9 s
(21 in `tests/pilot_fund`, 16 in `tests/integration/test_pilot_fund.py`; the baseline was 26). The draft's own
`test_tools.py` passes (2). `ruff check`, `ruff format --check`, and strict `mypy` (examples included) are clean.
