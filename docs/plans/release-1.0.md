# 1.0: robust at five mock funds

Status: proposed on 2026-11-23. 1.0 is tagged only on the owner's word, after the mock-fund rounds in
[part 3](#3-mock-fund-rounds) stop finding generic gaps. Until then each finished piece ships as a pull
request against `main`, and the version stays 0.9.x.

0.8.0 showed that one fund could adopt the platform. The four mock funds that followed recorded thirteen
generic gaps, and 0.8.0 and 0.9.0 closed six. 1.0 is the release a fund can rely on: its public surfaces
are stable, it speaks the protocol its AI clients speak today, and five funds of different shapes adopt it
without hand-written workarounds.

## 1. What 1.0 must show

| # | Criterion | Shown by | State |
| --- | --- | --- | --- |
| C1 | **Current protocol.** Clients that speak MCP 2026-07-28 get it; clients on 2025-11-25 or earlier keep the handshake; tools, results, and refusals are the same in both, over stdio and HTTP | Contract tests in both eras (decision 0034, on W1) | Built, W1 |
| C2 | **The one-hour test.** From a clean install, a fund's returns file and its administrator's own workbook (percent, title rows) give a horizon table and a reconciliation that lists each break | The generated `performance-report`'s tests, and a round's scripted run from a built wheel | Built, W2 |
| C3 | **A record of AI use.** For a period: who called which tool on which datasets, what was refused, and what needed an approval, with no content, as a file a compliance officer can keep | `qaos runs report`, tested on a run store with refusals, approvals, and a prune | W3 |
| C4 | **Results seen in the chat.** A performance analysis renders as a page in clients that support MCP Apps, and stays text everywhere else | Contract tests of the view's metadata, resource, and fallback; a manual check in one client | W5 |
| C5 | **Runs in a fund's cloud.** A readiness check of the stores, JSON logs, and secrets read from files, documented for a container service on Azure and on AWS | Tests, and the mock funds' deployment descriptors using them | W4 |
| C6 | **Robust at five funds.** Each fund's own tests and drill pass against a wheel built from the candidate, and each round's generic findings are fixed or written down with a next step | [Part 3](#3-mock-fund-rounds) | Rounds |
| C7 | **Security re-reviewed.** JWKS sign-in (0031), the caller assertion and per-tool approvals (0030), the SDK 2 transport (0034), and the surfaces added here | `docs/security-review.md`, third review | W7 |
| C8 | **A stated promise.** Which surfaces semver covers from 1.0, and how one changes | Decision 0035 | W8 |
| C9 | **Docs true.** The README says what it does for AI clients and installs what the getting-started path needs; the demo guide runs from a generated application to a question in an AI client | `tests/contract/test_docs.py`, and a rehearsal | W8 |
| C10 | **Quant Toolbox 1.0.** The numbers every generated application and tool share: frequency, horizons, and stale-data checks in its public API, held by known answers computed independently of both metrics backends, with its API reviewed, locked, and promised under semver | Its own suite, the API lock, and the rounds | W10 |

**Python stays 3.11 to 3.15 for 1.0.** A firm's own server imports the firm's code into the same interpreter
(decision 0020), so the platform's lowest Python is the lowest a fund's code may need. 3.11 has upstream
support until October 2027, and nothing 1.0 needs requires 3.12; MCP SDK 2 supports 3.10. Dropping 3.11 is
a 1.x decision, announced a release ahead.

## 2. Workstreams

Each workstream is one branch and one pull request against `main`. W3 to W5 are built by agents in their
own worktrees, from briefs that name the design; each agent runs the test, lint, format, and type-check
commands before handing back, and its result is reviewed before the pull request opens.

| # | Workstream | Branch | Delivers | Invariants touched |
| --- | --- | --- | --- | --- |
| W1 | **MCP 2026-07-28** | `feat/mcp-2026-07-28` | MCP SDK 2; both eras served and tested (decision 0034, on W1) | MCP only adapts the registry |
| W2 | **The one-hour test** | `fix/reconcile-reference` | `performance-report` reads the reference file's own unit and layout from `[reference]`, and leaves the benchmark out of the comparison | Templates stay MIT and carry their own tests |
| W3 | **AI-use report** | `feat/ai-use-report` | `qaos runs report --from --to`: totals, and per principal, tool, and dataset, with refusals, approvals, and the platform versions seen, as Markdown, CSV, or JSON; it says what it cannot see, such as pruned calls | An operator command, not a tool; no content leaves the run store |
| W4 | **Cloud operations** | `feat/cloud-ops` | `/readyz`, `QUANT_AGENT_OS_LOG_FORMAT=json`, `NAME_FILE` for every secret the platform reads from the environment, and a deployment section for Azure Container Apps and Amazon ECS | Secrets never logged; fails closed when a secret is given twice |
| W5 | **A performance view (MCP Apps)** | `feat/mcp-app-performance` | A static `ui://` page for `analyze_performance`, served when the tool is, advertised as the `io.modelcontextprotocol/ui` extension, with the tool's text result unchanged for every other client | Business logic stays out of MCP; the page draws only what the tool returned |
| W6 | **Mock-fund rounds** | `integration/1.0-rN`, then fixes on owning branches | [Part 3](#3-mock-fund-rounds) | Funds' facilities import nothing of the platform |
| W7 | **Security re-review** | `docs/security-review-3` | A third review of C7's scope, with real servers in the configurations most likely to fail open | Findings fixed or accepted in writing |
| W8 | **Promise and docs** | `docs/1.0` | Decision 0035; README, getting started, the demo guide, and the product pages for 1.0 | Docs stay true; a quality claim cites a measurement |
| W10 | **Quant Toolbox 1.0** | `feat/toolbox-1.0` | Frequency inference (now in the `performance-report` template) and carried-forward and staleness checks (now in Quant Analytics) move into the toolbox's public API, and the template and analytics use them; horizons by the data's own frequency; known-answer tests against hand-computed references for annualisation, horizons, and drawdowns; a review of every public name before the lock is frozen; a stated semver promise | Toolbox imports nothing of the platform; its API changes only with the lock and its changelog |
| W11 | **A standard data model** | `feat/data-model` | Decision 0037: six standard tables defined in Quant Toolbox, a mapping file, built-in file and SQL adapters, generated stub adapters for sources finished later, and a `MappedDatasetStore`; the toolbox part before 1.0, the mapping proved at two funds in round 3 | Funds keep their systems; the platform maps, it does not replace |
| W9 | **Release** | `release/1.0.0` | Version, changelogs, pack pins, mirrors | Only on the owner's word |

## 3. Mock-fund rounds

The five funds are [Kestrel Point](../../examples/pilot-fund/README.md) (multi-asset, its own servers) and
the [four in `examples/mock-funds`](../../examples/mock-funds/README.md): a CLO manager and an alternatives
allocator on Azure, a long-only equity manager and a credit hedge fund on AWS. A round adopts the candidate
at all five and writes down what it finds.

**A round:**

1. **Build the candidate.** Merge the open 1.0 branches into `integration/1.0-rN` from `main`, and build
   both wheels from it.
2. **Adopt it at each fund.** Install the wheels into a clean folder with its own `HOME`, run the fund's
   tests and site against them, and take up what the round adds: a 2026-07-28 client in the fund's own
   check, the AI-use report for its compliance file, readiness and JSON logs in its deployment descriptor,
   secrets from files, the performance view where the fund reports performance, and its administrator's
   own workbook through `performance-report`.
3. **Add one realistic friction per fund.** Something a fund of that shape meets in its first month and the
   rounds have not tried yet, such as a late administrator restatement, a holiday calendar, a renamed
   column, a revoked user, or a slow vendor service. Each round names its five in its notes.
4. **Record.** Each fund's `FINDINGS.md` gains a "Round N" table: what happened, measured or inferred, and
   the next step. The cross-fund table in `examples/mock-funds/README.md` ranks the generic ones.
5. **Fix.** A generic finding that blocks adoption is fixed on the branch that owns it, or a new one, before
   the next round. Anything else is written down with a next step.

**Exit.** A round in which no fund meets a new generic finding that blocks adoption, every fund's tests and
the Kestrel drill pass from built wheels on Linux CI, and C1 to C9 hold. Then W7, W8, and W9.

**Round 1** (2026-11-24, on `integration/1.0-r1`; findings in each fund's `FINDINGS.md` on the
`round1/*` branches). Every fund's tests and the Kestrel drill passed. Generic findings that blocked adoption:
readiness that read nothing real, monthly data annualised as daily in `performance-report`, one benchmark per
returns file, stale prices used silently in risk, and a firm image without drivers. All were fixed and merged
(#98, #101, #104), with service errors, refusals by firm tools, SQL dataset errors, the view's layout, and the
AI-use report's times and approvers. Left for later rounds or 1.x: vintages for restated figures, and the
tool-output gaps the view shows (window dates, benchmark name, risk-free rate, VaR sign).

**Candidates the rounds decide.** Each enters 1.0 only if a round shows it blocks adoption at a fund:

| Gap (cross-fund row) | Next step |
| --- | --- |
| Run store on PostgreSQL (6) | Needed for more than one replica; a single-host first deployment does without |
| String and date columns in datasets (4) | Changes the dataset model, so decide before C8 freezes it |
| Finer entitlements: rows, columns, documents (7) | A security design for the owner to decide first |
| Office files and object stores at ingestion (12) | Waits for a fund whose documents need it |
| `qaos legacy scan` misreads of module state (13) | Fix the cases the rounds keep meeting |
| Analytics each kind of fund needs (10) | Quant Toolbox families, one at a time, when a round needs one |

## 4. Order of work

1. This plan, as one pull request.
2. W1 and W2, as two pull requests; W5 builds on W1's SDK and goes after it.
3. W3, W4, and W5 in parallel, each by an agent in its own worktree.
4. Round 1 on `integration/1.0-r1`; fixes; further rounds until the exit holds.
5. W10 before round 2's candidate, so the rounds test the toolbox it promises.
6. W7 and W8; then W9, on the owner's word.
