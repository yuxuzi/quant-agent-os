# Open backlog and questions

What is still open. How the system works is in [architecture.md](architecture.md), and why is in
[decisions/](decisions/). Finished plans are in [plans/archive/](plans/archive/), including
[the earlier engineering plan](plans/archive/engineering-plan.md) that defines the `B-` backlog ids.
The most recent plan was [simplification.md](plans/archive/simplification.md).

## Open backlog

| Item | State | Next step |
| --- | --- | --- |
| Next release | P3 to P6 are unreleased, so getting started notes which options need `@main` | The owner's decision; a release removes that note |
| Updating generated projects | `pytemplate update` cannot reach a project from a built-in pack, which records a local path with no tags, so fixes are applied by hand | Kept open by the owner's decision until a user needs it; the likely route is the tagged mirror |
| Name availability (B-22) | All names are provisional | Check names before any public launch |
| Dataset limits | 20 MB, 200,000 rows, and 500 columns may be small for a firm's data | Decide with a real use |
| Hosted embedders | No measured score floor (still 0.2), so off-topic questions can return passages | Measure one if a user chooses hosted embeddings |
| Local embedding model | Not pinned to a file hash. CI runs a fake model, because `local-embeddings` is not in `all`; the real model ran locally on Python 3.13 | Pin the model; add a CI job if it becomes a default |
| Security findings F-2, F-3, F-4 | Partly fixed or accepted for now | See [security-review.md](security-review.md) |

## Waiting for a named design partner

Not started, and each needs a user asking for it: `run_backtest` as a platform tool, market-data
connectors, an LLM extractor, `.ipynb` or `.docx` loaders, SSO separate from `qaos hosted`, and any model
provider. Decision 0016 closes server-side synthesis, spec drafting, and code generation (B-12, B-15, B-16,
and B-20) until then.

## Open questions

Question numbers are the ones used in the earlier plan.

- **O-2:** Which MCP SDK version range should be supported as the protocol moves?
- **O-4:** Where is the license boundary for commercial features: MIT everywhere plus services, or a
  separately licensed package? No decision is needed while the customer is a firm hosting it themselves.
- **O-11:** When `factory.toml` moves to Core, and whether a keychain backend is added, are decided by the
  second product that needs those settings.
- **O-12:** Will pytemplate-uv's roadmap include generic pack authoring that `template fork` can delegate
  to? That is tracked in pytemplate-uv's repository.

A firm with Okta or similar will likely want OAuth without tenants. If the first partner asks, split OAuth
from `qaos hosted` then.
