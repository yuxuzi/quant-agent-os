# Open backlog and questions

What is still open. How the system works is in [architecture.md](architecture.md), and why is in
[decisions/](decisions/). Finished plans are in [plans/archive/](plans/archive/), including
[the earlier engineering plan](plans/archive/engineering-plan.md) that defines the `B-` backlog ids.
The most recent plan was [0.3.0, ready for a first pilot](plans/archive/pilot-ready-release.md),
released as 0.3.0. [Templates as starting points](plans/templates.md) followed it.

## Open backlog

| Item | State | Next step |
| --- | --- | --- |
| Name availability (B-22) | All names are provisional | Check names before any public launch |
| Dataset limits | 20 MB, 200,000 rows, and 500 columns may be small for a firm's data | Decide with a real use |
| Hosted embedders | No measured score floor (still 0.2), so off-topic questions can return passages | Measure one if a user chooses hosted embeddings |
| Local embedding model | Not pinned to a file hash. CI runs a fake model, because `local-embeddings` is not in `all`; the real model ran locally on Python 3.13 | Pin the model; add a CI job if it becomes a default |
| Pilot follow-ups | Deferred by the 0.3.0 plan until a real deployment shows what is needed: reusable entitlement adapters, per-document access, a support bundle, a published image, and slow tools in a killable process (F-2) | Learn from the first pilot |
| Security findings F-2, F-3, F-4 | Partly fixed or accepted for now | See [security-review.md](security-review.md) |

## Waiting for a named design partner

Not started, and each needs a user asking for it: `run_backtest` as a platform tool, market-data
connectors, an LLM extractor, `.ipynb` or `.docx` loaders, serving OAuth sign-in from `qaos serve`, and any model
provider. Decision 0016 closes server-side synthesis, spec drafting, and code generation (B-12, B-15, B-16,
and B-20) until then.

## Open questions

Question numbers are the ones used in the earlier plan.

- **O-2:** Which MCP SDK version range should be supported as the protocol moves?
- **O-11:** When `factory.toml` moves to Core, and whether a keychain backend is added, are decided by the
  second product that needs those settings.
- **O-12:** Will pytemplate-uv's roadmap include generic pack authoring that `template fork` can delegate
  to? That is tracked in pytemplate-uv's repository.

A firm with Okta or similar will likely want OAuth sign-in on `qaos serve`. The token checks are in
`quant_platform.identity`; wiring them in is a security change to decide when the first partner asks.
