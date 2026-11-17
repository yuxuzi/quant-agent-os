# 0026: One toolbox under Quant Analytics and the generated applications

- Status: accepted (direction from the owner, 2026-11-17)
- Date: 2026-11-17
- Amends: [0009](0009-quant-baseline-templates.md), [0010](0010-analytics-on-empyrical.md),
  [0018](0018-one-wheel.md), [0019](0019-business-source-license.md), [0022](0022-one-application-per-stage.md)

## Context

Each of the four `quant-apps` applications and `quant-lab` carried its own copy of the same library code:
five copies of `metrics.py`, four of the table format, and two or three each of the report document, run
folders, signal evaluation, risk measures, and synthetic data. About half of a generated application was code
no firm should edit. A fix reached new projects only.

Quant Analytics computed the same metrics again, through empyrical, and decomposed risk in its own code.
`test_metric_parity` held nine metrics together; risk decomposition, conventions text, data warnings, and
number formatting each existed twice with nothing holding them together.

Decision 0009 foresaw the answer: "if the analytics package ships, `metrics` may become a dependency on it
rather than generated code. That would be a later, deliberate change."

## Decision

- **`quant_toolbox`, product name Quant Toolbox, is a sixth import name in the one wheel.** It is a library:
  it registers no tools, serves nothing, and has no command. Seven subpackages in layers, each importing only
  earlier ones: `data`; `performance`; `risk` and `research`; `report` and `audit`; `testing`.
- **It is the one implementation.** Quant Analytics computes through it, and `quant_analytics.metrics` and
  `quant_analytics.risk` are gone. Generated applications import it, and hold only their settings, `firm/`
  code, command line, orchestration, and report sections.
- **MIT, like the packs.** Decision 0019's list of what stays MIT gains `src/quant_toolbox/`, so a generated
  project runs MIT code only. It imports nothing of the platform, and a test holds that.
- **empyrical first, numpy when it is not installed.** The metrics empyrical has run through it when it
  imports, and through the toolbox's numpy formulas otherwise. Before calling empyrical, the toolbox drops
  missing values, answers NaN for a series that does not vary, and turns infinities into NaN, so both answer
  the same: a test holds them to 1e-12 on clean data and on the edge cases where they would differ.
  `QUANT_TOOLBOX_BACKEND`, or a call's `backend` argument, forces one; every result's conventions name it.
  This keeps decision 0010's reason for empyrical, formulas a trusted library checks, with one
  implementation instead of two.
- **Only numpy is required.** pygal (static charts, in the `toolbox` extra), plotly (`interactive`),
  openpyxl (`excel`), empyrical (`empyrical`), and pandas load inside the function that needs them; a
  missing one raises `MissingExtraError` naming the install command. Charts are static SVG by default, with
  no script; plotly's graph objects draw interactive ones, never plotly express, which needs pandas.
- **Its public surface is locked.** `tests/toolbox/api.lock.json` records every public name and signature.
  A change fails until the lock is rewritten and the changelog says so. While the toolbox is 0.x, removing
  or changing a name is a minor version bump.
- **A generated project pins the release that generated it.** It depends on `quant-agent-os[toolbox]` from
  `git+https://github.com/yuxuzi/quant-agent-os@v<version>`; to take a newer toolbox, move the tag and run
  the project's tests. The packs become `quant-apps` 2.0.0 and `quant-baseline` 4.0.0, and `quant-lab`
  loses its `metrics` component.

## Consequences

- A metric, table, report, or run-folder fix reaches every project that moves its tag, without regenerating.
- A generated project is no longer free of Quant-Agent-OS: it installs this wheel, for its MIT toolbox only;
  the platform's server is still a dependency only with `agent-tools`.
- The analytics toolset needs numpy rather than empyrical, so it also runs on the numpy backend.
- The wheel's `analytics` extra is `toolbox` plus `empyrical`; a generated application installs numpy and
  pygal, not empyrical, so pandas stays out unless a firm adds the `empyrical` extra.
- Core keeps its own stdlib frequency inference, because Core runs without numpy; the toolbox has none.
- `tests/factory/test_metric_parity.py` is replaced by `tests/toolbox/test_backends.py`, and the pack tests
  that held copies byte-identical by a test that no application carries a copy.
