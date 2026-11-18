# 0.8.0: a pilot at a mock multi-asset fund

Status: plan, revised on 2026-11-17. The first draft listed proposals for the owner to choose from; the
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

Filled in by W5. Each row is one friction point the drill met.

| # | Where | What happened | Fixed by, or next step |
| --- | --- | --- | --- |
| | | | |

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
