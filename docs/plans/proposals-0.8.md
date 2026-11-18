# Proposals after 0.7.0: features, design, and user experience

Status: proposed on 2026-11-17, for the owner to choose from. Nothing here is started. Each item says which
invariant it touches, so a choice does not reopen a decision by accident. Items are ranked within each part by
value for a fund's first pilot against the work they take.

0.7.0 moved App Factory to pytemplate-uv 3.2 and brought Quant Analytics and the `quant-apps` applications onto
Quant Toolbox 0.6.0. The proposals start from what that left out.

## 1. New features

| # | Proposal | Why | Size | Invariants touched |
| --- | --- | --- | --- | --- |
| F1 | **`analyze_signal`**, a read-only tool: information coefficient, rank IC and its IR, quantile returns, spread, and decay of a signal dataset against forward returns, both by reference | The research and analytics products meet here, and every PM asks it first; Quant Toolbox already computes all of it, vectorised | M | Data by reference; efficiency delta (one new tool) |
| F2 | **Historical stress in `analyze_risk`**: Toolbox `SCENARIOS` applied to today's weights when the asset returns are daily and dated across a window, None otherwise | Risk managers report these windows; the toolbox refuses a short window rather than mislabel it, so the tool inherits an honest answer | S | Additive fields only |
| F3 | **`tear_sheet` resource**: `qaos://tear-sheet/{dataset}/{column}` renders Toolbox `report.tear_sheet` to static HTML (no script) for a client to show or save | Investor relations asks for the same page each month; a resource keeps numbers out of the model | M | Writes stay local (a resource returns text; the client saves it) |
| F4 | **Factor risk from a vendor model** in `analyze_risk`: loadings, factor covariance, and specific variances by dataset id, through Toolbox `factor_risk` | Funds with Barra or Axioma want their own model's numbers, not OLS betas | M | Data by reference; needs three datasets per call |
| F5 | **Brinson attribution** as a field of `analyze_performance` when group weights and returns are given | Single-period Brinson-Fachler is exact in the toolbox; multi-period linking is not, so the output must say so | S | Facts versus interpretation: state "single period" in conventions |
| F6 | **Liquidity profile** (Form PF buckets) when an average-daily-value dataset is named | Needed for regulatory reporting, but only when a firm has ADV data | S | Optional input; refuse rather than guess ADV |
| F7 | **A `ci` component for `quant-apps` and `quant-baseline`**: GitHub Actions pinned to commit SHAs, no persisted credentials, and Dependabot with a 7-day cooldown, as pytemplate-uv 3.2 gives its own profiles | Generated applications have no CI today, so a firm's edits go untested until someone adds it | S | Pack version bump and mirror republish |
| F8 | **Build and publish the `quant-toolbox` wheel** with each release | The 0.6.0 notes say installing from a wheel or an index needs it, and the release does not build it | S | Release workflow change: ask first |
| F9 | **`qaos app status`**, if pytemplate-uv adds generation status to its public API | 3.2 has `pytemplate status`, but only in its command line; App Factory may import only `pytemplate.api` | XS once available | Decision 0006: wait for the public API, never import around it |

Not proposed, because they wait for a named design partner: `run_backtest` as a platform tool, market-data
connectors, OAuth sign-in on `qaos serve`, and any model provider ([open backlog](../ENGINEERING_REVIEW_PLAN.md)).

**Recommendation:** F2, F7, and F8 for 0.8.0; they are small and close gaps users already hit. Then F1, the
largest gain for a research team, measured in the agent-efficiency evaluation before it ships.

## 2. Design

| # | Proposal | Why |
| --- | --- | --- |
| D1 | **Tag the toolbox on its own** (`quant-toolbox-v0.6.0`) as well as the platform tag | Decision 0027 gives it its own version, but generated applications pin the platform tag, so a toolbox fix waits for a platform release |
| D2 | **Split `quant_app_factory/cli.py`** (about 800 lines) into one module per command group: `new`, `template`, `settings` | It is the largest command module, and the interactive questions are hard to test in one file |
| D3 | **One output schema for refusals.** Every refusal carries `reason`, `next_step`, and, for data, the dataset id, across tools and commands | Errors already name a next step in prose; a field lets a client show it without parsing text |
| D4 | **A summary field first in analytics outputs**: the five numbers an agent quotes, then the detail | The efficiency evaluation counts output tokens; clients often need only the headline |
| D5 | **Remove the repeated "per-caller access fails closed" invariant in `AGENTS.md`** (done in 0.7.0) | Two wordings of one rule invite drift |

## 3. User experience

| # | Proposal | Why |
| --- | --- | --- |
| U1 | **Group `qaos --help`** into Set up (`init`, `doctor`, `connect`), Serve (`serve`, `catalog`, `eval`), Data and research (`data`, `ingest`, `search`, `strategy`), Apps (`app`), and Operate (`keys`, `runs`) with Typer help panels | Fourteen commands in one list; a first user cannot tell which three to run |
| U2 | **`qaos init --sample --connect claude`**: one command from nothing to a client that sees the sample tools | Getting started takes three commands today; `connect --write` already exists |
| U3 | **`--json` on every listing command** (`data list`, `runs list`, `catalog`, `app template list`) | Only research commands have it, so scripts parse tables |
| U4 | **`qaos runs show RUN --open`** renders a recorded run's report, including a tear sheet, to a local HTML file and opens it | Runs are recorded but only readable as JSON |
| U5 | **`qaos doctor` prints the exact command for each fix** and exits non-zero when anything is off | It already says how to fix; an exit code makes it usable in a deployment check |
| U6 | **A role index on the docs home page**: CEO or CTO, PM, risk manager, researcher, and operator, each linking to the two pages it needs | The tutorial is written for leaders; the other roles still start from the README |

**Recommendation:** U1, U3, and U5 together in one small release; none changes a security default or a tool.
