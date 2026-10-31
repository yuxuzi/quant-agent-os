# 0006: Quant App Factory imports pytemplate-uv as a library

- Status: accepted
- Date: 2026-10-31
- Supersedes: the pytemplate-uv rules in [decision 0003](0003-brand-and-product-structure.md)

## Context

Decision 0003 had App Factory run `pytemplate-uv` only as a pinned CLI
(`uvx pytemplate-uv==3.0.*`) and parse its JSON. That has costs:
- **Subprocess.** Every plan and create starts a subprocess.
- **Network on first use.** It needs network access the first time, to fetch the tool.
- **Approval for previews.** The exact file preview needs `execute_code`, so it cannot be a
  read-only tool.
- **Weaker tests.** Tests could only replay recorded JSON, not render.

`pytemplate-uv` 3.0 ships a supported, typed Python interface, `pytemplate.api`. It has
`RenderRequest`, `plan`, `render`, and typed errors, and its compatibility policy covers it.
The maintainer (the same author as this project) asked for it to be used like any other
third-party library.

## Decision

- **Dependency.** `quant-agent-os-factory` depends on `pytemplate-uv>=3.0,<4`.
- **Only the adapter imports it.** `quant_app_factory/infrastructure/pytemplate_uv.py` is the
  only module that imports it, and only `pytemplate.api`, never internal modules. Core and
  Quant Research never import it. Tests enforce both rules.
- **The port stays.** `ScaffoldProvider` is unchanged, so another scaffolder can still replace
  it.
- **In-process rendering.** Rendering runs in-process with Copier's `unsafe=False`. The bundled
  profiles and the factory's local `agent-tool` pack render without network access.
- **`plan_app` previews the exact files.** pytemplate-uv renders into a temporary directory,
  never the target, so `plan_app` stays read-only. `create_app` keeps `write_local_data`,
  `execute_code` (the generated project's `uv` checks), and `network` (`uv sync`).
- **Cool-off.** The supply-chain cool-off applies to its new transitive dependencies (copier
  and its dependencies). `pytemplate-uv` itself is exempt as first-party.
- **Still out of scope.** pytemplate-uv's code is not vendored or copied, and this repository
  holds no plans for it.

## Consequences

- Planning and creating are faster and work offline, apart from the generated project's own
  `uv sync`.
- Offline tests render real projects, instead of replaying recorded JSON.
- Open question O-5, a narrower permission for running the pinned CLI, is no longer needed.
- Upgrading to pytemplate-uv 4 is a deliberate change of the version range in one
  `pyproject.toml`.
