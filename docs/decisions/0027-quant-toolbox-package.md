# 0027: Quant Toolbox is its own package in the repository

- Status: accepted (direction from the owner, 2026-11-17)
- Date: 2026-11-17
- Amends: [0018](0018-one-wheel.md), [0026](0026-quant-toolbox.md)

## Context

Decision 0026 put Quant Toolbox in the one wheel as a sixth import name. A generated application then
installed `quant-agent-os[toolbox]`: the whole platform's code, under the platform's business source license,
for an MIT library that imports none of it. Its version was the platform's, so a toolbox fix waited for a
platform release, and a toolbox user read the platform's changelog to find toolbox changes.

Decision 0018 had merged earlier workspace members into one wheel because releases are GitHub-only and
installing several wheels needed `--find-links`. On 2026-11-17 three installs from a git clone of this branch
showed that this no longer applies to one workspace member:

| Install | Result |
| --- | --- |
| a project depending on `quant-toolbox` with a uv `git` source and `subdirectory = "packages/quant-toolbox"` | installs numpy and the toolbox only; no `quant_agent_os`, no pydantic |
| a project depending on `quant-agent-os[toolbox]` with a uv `git` source | uv resolves `quant-toolbox` from the same checkout's workspace |
| `uv tool install "quant-agent-os[analytics] @ git+...@<ref>"` | the same, with empyrical |

## Decision

- **`quant-toolbox` is a distribution of its own,** a uv workspace member at `packages/quant-toolbox`, with
  its own `pyproject.toml`, version, `CHANGELOG.md`, `README.md`, MIT `LICENSE`, and tests
  (`packages/quant-toolbox/tests`). The import name stays `quant_toolbox`.
- **Only numpy is required.** Its extras are `charts` (pygal), `interactive` (plotly), `excel` (openpyxl),
  `pandas`, `empyrical`, and `all`. A missing optional library's error names the quant-toolbox extra.
- **The platform depends on it by extra, at an exact version.** `quant-agent-os`'s `toolbox`, `interactive`,
  `excel`, and `empyrical` extras pin `quant-toolbox[<same purpose>]==<version>`, so the extras users already
  install keep working, and Core still runs without numpy.
- **One tag releases both.** A `v<version>` tag builds and attaches both wheels. The two versions may differ;
  `tests/contract/test_release.py` holds the platform's pin equal to the toolbox's version. A toolbox-only fix
  can still ship as a platform patch release.
- **Generated applications depend on `quant-toolbox` alone,** from
  `git+https://github.com/yuxuzi/quant-agent-os@v<version>` with `subdirectory = "packages/quant-toolbox"`,
  and on `quant-agent-os` only when they add the `agent-tools` component. `quant_agent_os_source` still names
  the repository. The packs become `quant-apps` 2.1.0 and `quant-baseline` 4.1.0.
- **Everything else in 0026 holds:** the layers, the locked API (now `packages/quant-toolbox/tests/api.lock.json`),
  empyrical first with numpy otherwise, and that it imports nothing of the platform.

## Consequences

- A generated project installs no business-source-licensed code unless it adds agent tools.
- The toolbox can be read, tested, and released as a library: `uv run --package quant-toolbox pytest
  packages/quant-toolbox/tests` runs its tests alone, and its changelog lists only its changes.
- The Docker image copies the workspace member, and `uv build --all-packages` builds two wheels.
- pip cannot resolve a workspace source from a git URL; installs from a tag need uv, which the project
  already requires.
