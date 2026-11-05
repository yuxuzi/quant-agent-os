# 0018: One wheel with extras

- Status: accepted
- Date: 2026-11-05
- Supersedes: the packaging part of [0003](0003-brand-and-product-structure.md)

## Context

Decision 0003 made each product a workspace member that could be released on its own. In practice
all five distributions shipped in lockstep with `==` pins, so the promised independence never
happened and every release paid for it. Releases are GitHub-only, so installing the wheels needed
`--find-links` to a folder of downloads, or a clone ([simplification plan](../plans/simplification.md)).

## Decision

- One distribution, `quant-agent-os`, holds the five import names under `src/`: `quant_agent_os`,
  `quant_research`, `quant_analytics`, `quant_app_factory`, and `quant_platform`. Their tests are in
  `tests/research`, `tests/analytics`, `tests/factory`, and `tests/server`.
- The import rules in 0003 still hold, and `tests/contract/test_import_boundaries.py` checks them:
  Core imports no product, and the products do not import each other.
- The base stays pydantic, pydantic-settings, rich, and typer, so generated projects stay light.
- Extras bring dependencies: `mcp`, `api`, `server` (both), `analytics`, `pdf`, `postgres`,
  `factory`, and `all`. Quant Research and the hosted command need nothing more, so they have no
  extra: an empty extra keeps nothing out (see [0017](0017-hosted-behind-a-command-group.md)).
- A command that needs a missing extra says which one to install, rather than failing on import.
- `.python-version` is 3.13, the version the plugin and the image use. CI still tests 3.11 to 3.15.

## Consequences

- `uv tool install "quant-agent-os[analytics,server] @ git+https://github.com/yuxuzi/quant-agent-os@TAG"`
  installs everything from one git tag, with no clone. A spike confirmed that `uv_build` packs the
  five modules and the template packs' odd file names, and that this install works.
- Products can no longer be released on separate versions. Splitting one out later is still possible,
  because the import boundaries hold.
- Every install carries all five modules' code, including those it has no dependencies for. This
  costs about 300 KB and imports nothing until it is used.
