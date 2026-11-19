# 0.8.0: a pilot at a mock multi-asset fund

Status: released as 0.8.0 on 2026-11-19; part 5 records what the pilot found and what iteration two fixed.
Revised on 2026-11-17. The first draft listed proposals for the owner to choose from; the
owner chose to drive 0.8.0 by installing the platform at a mock fund and fixing what that shows. The earlier
proposals are kept as a backlog in [part 6](#6-backlog-carried-from-the-first-draft), each marked with
whether the pilot pulls it in.

0.7.0 moved App Factory to pytemplate-uv 3.2 and brought Quant Analytics and the `quant-apps` applications
onto Quant Toolbox 0.6.0. What it cannot show is whether a fund could adopt the platform: install it in its
own facility, sign its people in through its own gateway, read its own warehouse and documents, answer its
standing research, backtest, and risk questions, and serve its existing code without rewriting it.

## 1. Goal and the test of it

After this iteration a fund's engineer can take the platform from a built wheel to tools their PMs, risk
managers, and researchers use, behind the fund's sign-in, with the fund's data, documents, and legacy code,
and every claim below is shown by a drill that runs offline.

| # | Criterion | Shown by |
| --- | --- | --- |
| B1 | **Install** from the wheel into a clean folder, with no network after the download, by following the runbook | The drill script, timed |
| B2 | **Sign-in** through the fund's identity provider; its groups decide who reaches what; API keys still work | Integration test with the mock gateway |
| B3 | **Data in place**: the fund's warehouse is read where it is, through one `DatasetStore` adapter, with desk entitlements | Integration test; refused datasets look missing (decision 0021) |
| B4 | **Research** over the fund's own documents, cited, with injected text left inert | Ingest plus a cited search in the drill |
| B5 | **Baseline analysis** answers the fund's standing questions on signals, backtests, and risk | Known-answer tests; agent-efficiency delta |
| B6 | **Legacy code served**: simple functions as functions, complicated code as a service, each passing `check_app` | Converter output in the drill, with the manual edits counted and reported |
| B7 | **Operate**: agreed cases pass, the audit trail says who called what, and the runbook covers backup and upgrade | `qaos eval`/`run_acceptance` and `qaos runs` in the drill |
| B8 | **Honest record**: every friction point the drill meets is fixed or written down with a next step | [Part 5](#5-what-the-pilot-found), filled in by the drill |

A quality claim cites a measurement; B6 in particular reports how much a developer had to change by hand.

## 2. The mock fund

**Kestrel Point Capital** is fictional, and so is all its data. It is a multi-asset fund with three desks
and two support teams:

- **Desks:** equity long/short (US and Europe), macro (government bond futures and G10 FX), and
  commodities (energy and metals futures). Credit is held as index exposure by the macro desk.
- **Teams:** risk (sees every desk) and research (sees signals and documents, not positions).
- **People in the drill:** a PM per desk, a risk manager, a researcher, and an operator, each a user in
  the fund's identity provider with a group per role.

Its facility lives in `examples/pilot-fund/facility/` and is written as the fund's code, not the platform's:
nothing in it imports `quant_agent_os` or `quant_platform`.

| Part | What it is | Why it is shaped this way |
| --- | --- | --- |
| `warehouse/` | A SQLite warehouse built deterministically: instruments, daily prices in long format, end-of-day positions, trades, signals, benchmark returns, average daily value, and a vendor factor model (loadings, factor covariance, specific variance) | Real warehouses are long and normalized, while the platform's datasets are wide; the adapter has to bridge that, which is the friction a fund meets |
| `kestrel_quant/` | The fund's legacy package. Simple functions (carry, momentum, and value signals; volatility targeting; a drawdown table) written as funds write them: pandas, module-level configuration, a global connection, printing. A complicated `risk_engine` with a slow model load, a cache, and multi-step VaR, stress, and factor decomposition | Simple code becomes functions; stateful code becomes a service. The smells are the converter's real input |
| `research/` | About fifteen documents: strategy memos, research notes, risk committee minutes, and one note carrying an injected instruction | Quant Research must cite them and leave the injection inert |
| `gateway/` | A mock identity provider: it issues tokens to the drill's users and answers RFC 7662 introspection with an audience, an issuer, a tenant, and groups | The platform is a resource server (decision 0013); the fund's provider issues the tokens |
| `access.toml` | Desk entitlements: each desk sees its own positions; risk sees all; research sees no positions | Per-caller access fails closed (decision 0021) |

What the fund writes to adopt the platform goes in `examples/pilot-fund/site/`: its server module with
`FirmApp`, the warehouse adapter, the facades and service the converter drafted and a developer finished,
its acceptance cases, and its settings. `examples/pilot-fund/drill.sh` builds the wheel, installs it into a
clean folder with its own `HOME`, and runs the fund's day from install to audit.

## 3. Workstreams

Each workstream is one branch and one PR against `main`, built by its own agent in its own worktree. W1 to
W4 run in parallel; W5 needs all four.

| # | Workstream | Branch | Delivers | Invariants touched |
| --- | --- | --- | --- | --- |
| W1 | **Mock fund facility** | `feat/pilot-fund-facility` | Part 2's `facility/`, deterministic and offline, with tests that the warehouse and the legacy code behave | All data fictional; offline tests; the facility imports nothing of the platform |
| W2 | **Opt-in OAuth sign-in** | `feat/oauth-sign-in` | `qaos serve` and `serve_apps` accept tokens checked by the existing `IntrospectionVerifier`, configured in settings, **off by default**; API keys keep working; the token's groups feed per-caller access; RFC 9728 metadata is published | A security default: the owner approved it as opt-in on 2026-11-17. A new decision record extends decision 0013 to a firm's own server and closes the open-backlog item |
| W3 | **Baseline analysis tools** | `feat/baseline-analysis` | In Quant Analytics, on Quant Toolbox: `analyze_signal` (IC, rank IC and IR, quantile returns and spread, decay; was F1), `run_backtest` (signal to weights to net returns with costs and trade lag, then the usual metrics), historical stress in `analyze_risk` (was F2), and factor risk from a vendor model (was F4) | Data by reference; documented conventions and known-answer tests; efficiency delta reported. `run_backtest` leaves the "waiting for a design partner" list by the owner's choice |
| W4 | **Legacy converter** | `feat/legacy-converter` | `qaos legacy scan PATH` inventories a package and classifies each function as *expose as is*, *needs a facade*, or *needs a service*; `qaos legacy wrap` drafts facades registered with `FirmApp.expose`, or a FastAPI service with `call_service` stubs, plus a `check_app` test; a `quant-legacy` skill steers the agent's scan, wrap, check, and fix loop | Decision 0016 and local writes: the server reads and writes no code; `qaos legacy` is a command the developer runs. Explicit registration: the draft is ordinary code the firm commits. A new decision record says the output is a draft |
| W5 | **Drill, runbook, and findings** | `feat/pilot-drill` | `drill.sh`, `site/`, an offline integration test of the fund's day, a pilot runbook in the docs, and part 5 filled in | Docs stay true; tests offline; efficiency measured |

**The converter is a developer's aid, not a promise.** It turns legacy code into a first draft that a developer
runs, debugs, and steers, most likely with a coding agent and the skill. It will not work first time on real
code; it says what it could not decide, and the docs say so. Its measure is how far the draft gets and how few
edits finish it (B6), not that it needs none.

## 4. Order of work

1. **Plan** (this document): one PR, docs only.
2. **W1 to W4 in parallel**, each tested on its own fixtures. W4 tests against a copy of a small legacy
   module of its own, so it need not wait for W1.
3. **W5** merges the four on a local integration branch, runs the drill, and writes down every friction point.
4. **Iteration two**: each friction point is fixed on the branch that owns it, or deferred with a next step
   in part 5. The drill reruns until B1 to B8 hold.
5. **Release 0.8.0** only on the owner's word, with a changelog entry per workstream.

## 5. What the pilot found

Filled in by W5 on 2026-11-18, and updated after iteration two on 2026-11-19. Each row is one friction point the
drill or the in-process test met. "Fixed" names the commit or pull request; anything else is written down with its
next step and owner.

| # | Where | What happened | Fixed by, or next step |
| --- | --- | --- | --- |
| P1 | W4 scan | `kestrel_quant.db` keeps a module-level query cache, and the scan passes "keeps state between calls" to every function that reaches it, so 20 of 26 callables came out as *service*, including the plain signals and the P&L report | **Fixed** in #85: a module memo cache, or a connection opened once, makes a facade with a `TODO(review)`; the scan now finds 8 services of 26, not 20 |
| P2 | W4 wrap | The drafted service built `RiskEngine()` but never called `load()`, its set-up method, so every risk endpoint answered 500 ("call load() first") | **Fixed** in `cd214ba` (W4): the lifespan calls a public, argument-free method named like set-up that is not served, with a `TODO(config)` |
| P3 | W4 wrap | The three signal facades returned whole panels, 315 to 528 KB, over the 100 KB output limit | **Fixed** in #85: a facade over code that returns a whole dates-by-names table takes `as_of` and returns one date's row, with a `TODO(review)`; a tool served as is gets the same note |
| P4 | W4 wrap | Optional data parameters got no example in `test_tools.py` (`{}`), so the test ran the legacy fallback that reads the warehouse directly; required untyped parameters got `1.0`, and `kpc_desk_pnl` "passed" with `desk = 1.0` and an empty report | **Fixed** in #85: optional data parameters get a dataset reference, and untyped names such as `desk` are filled as strings, each a `TODO(data)` |
| P5 | W4 draft | `server.py` imports `facades` and `stubs` by bare name, so a firm's own server module must put the draft's folder on `sys.path` | **Fixed** in #85: the draft is a package with relative imports |
| P6 | W4 draft | `_quiet()` uses `contextlib.redirect_stdout`, which is process-wide, while tools run on several threads | **Fixed** in #85: drafted facades run legacy code under one lock |
| P7 | Site, W4 | `kpc_desk_pnl` and the risk engine read the warehouse themselves, past `access.toml`'s dataset rules. A facade can check `current_caller()`, but a service cannot see who called | Facade scoped by hand with `permits` on `positions_<desk>`, refusals noted for the audit; `risk_*` granted to the risk group only, so a PM cannot get their own desk's VaR. **Fixed** in #86 (decision 0030): `call_service(..., forward_caller=True)` sends a signed, 60-second caller assertion the service checks with `verify_caller`. Kestrel's site still grants `risk_*` to the risk group only; scoping a PM's own desk in its service is the next step (W5) |
| P8 | W1, W3 | The planted ICs pair a signal on day t with day t+1's return, which is `lag: 0`; the platform's default `lag: 1` gives a rank IC of 0.0261 for the planted 0.0221, and the skill tells clients to keep the default. With `lag: 0` the IC and IR match to 1e-10, with an "optimistic" warning, and the HAC t (4.05) differs from the planted naive t (3.94) | **Fixed** in #77: the warehouse states its known answers at the default lag too (momentum rank IC 0.0261), matched to 1e-9 |
| P9 | W3 | `analyze_risk` takes `weights` only inline, so a client must get a desk's book elsewhere; the drill reads it from the warehouse itself | **Fixed** in #77: `analyze_risk` 2.3.0 takes `weights_ref`, a dataset's column or one date's row |
| P10 | W3 | Historical stress covers 2 of 12 windows, because the warehouse starts in 2023 | As designed; the tool's note says so, and the drill quotes it |
| P11 | Core, W2 | `qaos runs show` listed runs without their principal, so the operator could not see who called what without opening each run, though the deployment guide says runs show it | **Fixed** in `596e1a4`: the listing has a principal column |
| P12 | Acceptance | `run_acceptance` runs with no caller, so a catalog with an access policy refuses every dataset (fail closed). The fund runs its cases on a second catalog without access: they check numbers, not entitlements, which only the drill and the test check | **Fixed** in #77: agreed cases run as a named `[caller]` with the catalog's access applied |
| P13 | Settings | The risk tools need `network` granted and pre-approved, and pre-approval is server-wide, so any other tool needing `network`, such as a hosted embedder, would also run without approval | **Fixed** in #86 (decision 0030, approved by the owner): `QUANT_AGENT_OS_PREAPPROVED_TOOLS` names the tools; Kestrel pre-approves `network` for its six `risk_*` tools only, and the server-wide form warns at start-up |
| P14 | Docs | Backup and upgrade in [operations](../operations.md) cover only Docker; a fund installing the wheel had no steps | **Fixed** in `908bd15`: the [pilot runbook](../pilot-runbook.md#8-operate) covers both for a wheel install. Neither is drilled for a wheel yet |
| P15 | Testing a firm's server | `serve_http` closes its run store when serving stops, and the site's `serve()` holds the corpus open while it serves, so the in-process test patches both to drive the HTTP app | **Fixed** in #77: `quant_platform.build_server_app` yields the app `serve_apps` would serve, for in-process tests |
| P16 | Refusals | A refused desk reads `DeskRefusedError: No desk 'macro'; the desks are eq_ls.`, and a refused tool returns `denied` over REST with HTTP 200, so a client parses text and statuses to tell refusals apart | Recorded under D3 (one output schema for refusals) |
| P17 | Legacy package | `kestrel_quant` reads `KPC_WAREHOUSE` into `CONFIG` at import, so the variable must be set before anything imports it; the in-process test sets `CONFIG` too | A legacy smell facades cannot fix; the runbook's settings set it where the server starts |
| P18 | Type checks | The repository's strict mypy could not find the untyped `kestrel_quant` the facades import | Fixed in `1122f33` with a mypy override for the pilot's legacy package |
| P19 | The drill | One run started in the background stalled for 15 minutes in phase 7: the server and the risk service were listening but did not answer, the wait's `curl` had no time limit, and the run resumed only after a manual probe. The cause was not pinned down | The drill now gives every `curl` a 5-second limit and a 180-second start deadline, starts its services with stdin from `/dev/null` and `uv run --no-sync`, and has passed in about 30 seconds since. Seen once more on 2026-11-19, outside the drill: the tutorial's first `uv run python serve.py` after `uv add` printed nothing for about 15 minutes with the process idle, then served; a second start took 2 s. The tutorial's troubleshooting table says so. Next step (W5): rerun on Linux CI before trusting it unattended, and catch a stalled start with a stack dump (`faulthandler`) |

### Results against B1 to B8

Measured on macOS with a warm uv cache, from the drill's own summary (`examples/pilot-fund/drill.sh`) and
`tests/integration/test_pilot_fund.py` (10 tests).

| Drill phase | Seconds |
| --- | --- |
| 1. Build both wheels | 0.1 |
| 2. Install into a clean folder (the only phase allowed the network) | 2.3 |
| 3. Build the warehouse | 1.6 |
| 4. `qaos init` and `qaos doctor` | 0.4 |
| 5. Ingest the 16 documents | 0.2 |
| 6. Scan, wrap, and test the finished legacy draft | 10.3 |
| 7. Start the gateway, the risk service (with its 1-second model load), and the server | 12.0 |
| 8. The fund's day: 19 checks as five signed-in users, a disabled one, and no one, over REST and MCP | 1.0 |
| 9. The agreed cases | 1.7 |
| 10. `qaos runs show` | 0.2 |
| **Total** | **29.8** |

| # | Holds? | Measurement |
| --- | --- | --- |
| B1 | Yes, with a warm cache | Both wheels built and installed into a fresh `uv` project with its own `HOME`; every phase after the install runs with `UV_OFFLINE=1`. An install from a cold cache was not measured |
| B2 | Yes | Five users signed in through the gateway, and a disabled one gets no token; no credential gets 401 with the RFC 9728 pointer; groups decide tools; an API key with a group works beside sign-in (integration test) |
| B3 | Yes | One adapter, `warehouse_store.py` (about 240 lines), serves 19 datasets read in place; a PM's request for another desk's returns answers word for word as a missing dataset; research reaches no positions |
| B4 | Yes | 16 documents ingested; the cited search returns the injected note with its text marked untrusted, and the run store shows only the call made |
| B5 | Yes | `analyze_signal` recovers the planted momentum rank IC, 0.0221294799, to 1e-10 at `lag: 0` (P8), and carry's -0.0117901681; `run_backtest` of momentum at 5 bps; `analyze_risk` on the macro book with the vendor model and 2 stress windows. The agent-efficiency evaluation is unchanged: the platform's tool surface did not change |
| B6 | Yes, with edits counted | Scan: 26 callables. Inventory: 38 lines changed. Draft: 13 tools (7 facades, 6 service endpoints), 110 TODO lines; its contract test passed for 5 of 13 tools as drafted (after P2's fix). Finishing it changed 342 added and 312 removed lines of the 731-line draft, across six files; both generated tests pass, and the tools pass `check_app` on the real warehouse. After iteration two (P1, P3 to P6) the scan finds 18 facades and 8 services, and the reviewed inventory drafts 107 TODO lines. The drill's diff against the finished draft (676 lines) now compares the new converter's draft with one finished from the first, so it no longer counts the edits a developer would make; re-finishing from the new draft is the next measurement (W4) |
| B7 | Mostly | Agreed cases: 6 of 6 pass (`acceptance.toml`); `qaos runs show` names each caller (after P11); the runbook covers backup and upgrade for a wheel install (P14), but the drill does not rehearse them |
| B8 | Yes | This table: 19 friction points. 5 fixed on the drill's branch (P2, P11, P14, P18, P19), 11 in iteration two (P1, P3 to P9, P12, P13, P15); P10 and P17 are as designed or the legacy code's own, and P16 waits on D3 |

## 6. Backlog carried from the first draft

| # | Proposal | State in this plan |
| --- | --- | --- |
| F1 | `analyze_signal`, a read-only tool for a signal's IC, quantile returns, spread, and decay | In W3 |
| F2 | Historical stress in `analyze_risk` | In W3 |
| F3 | `tear_sheet` resource rendering Toolbox `report.tear_sheet` to static HTML | Backlog |
| F4 | Factor risk from a vendor model in `analyze_risk` | In W3; the mock fund has a vendor model |
| F5 | Single-period Brinson attribution in `analyze_performance` | Backlog |
| F6 | Liquidity profile (Form PF buckets) from an ADV dataset | Backlog; the warehouse carries ADV so it can follow |
| F7 | A `ci` component for `quant-apps` and `quant-baseline` | Backlog |
| F8 | Build and publish the `quant-toolbox` wheel with each release | Backlog; release workflow change |
| F9 | `qaos app status`, once pytemplate-uv's public API has it | Waits for pytemplate-uv (decision 0006) |
| D1 | Tag the toolbox on its own | Backlog |
| D2 | Split `quant_app_factory/cli.py` by command group | Backlog; W4 adds `qaos legacy` as its own module rather than growing it |
| D3 | One output schema for refusals | Backlog; the drill records refusal text a client had to parse |
| D4 | A summary field first in analytics outputs | Backlog; W3's new tools put the headline first |
| D5 | Remove the repeated access invariant in `AGENTS.md` | Done in 0.7.0 |
| U1 | Group `qaos --help` into panels | Backlog; W4 and W2 add commands and settings, which makes it more pressing |
| U2 | `qaos init --sample --connect claude` | Backlog |
| U3 | `--json` on every listing command | Backlog |
| U4 | `qaos runs show RUN --open` | Backlog |
| U5 | `qaos doctor` prints each fix and exits non-zero | Pulled into W5 if the drill's install check needs it |
| U6 | A role index on the docs home page | Backlog; the runbook names each drill user's role |

Still waiting for a named design partner: market-data connectors, an LLM extractor, `.ipynb` or `.docx`
loaders, and any model provider ([open backlog](../ENGINEERING_REVIEW_PLAN.md)).
