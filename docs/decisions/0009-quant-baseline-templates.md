# 0009: Quant baseline templates ship with App Factory as portable apps

- Status: proposed
- Date: 2026-10-31

## Context

The quant work people most often start from scratch is the same each time: researching a
signal, backtesting a strategy, and reporting the results. pytemplate-uv's bundled profiles
are general-purpose. Quant-Agent-OS can encode the quant conventions instead: metric
definitions, annualisation, cost models, walk-forward evaluation, and keeping computed facts
apart from interpretation.

## Decision

- **A `quant-baseline` template pack ships inside `quant_app_factory/packs/`.** Its profiles
  are `alpha-research`, `backtest`, and `report`, with these shared components:
  - `data-csv` (the default), `data-parquet`, and `data-database`;
  - `agent-tools`;
  - `container`.
- **The generated apps are portable.** They do not depend on Quant-Agent-OS. The optional
  `agent-tools` component adds typed `@tool` wrappers, `check_tool` tests, and a deliberate
  dependency on `quant-agent-os`, so the app's functions can be registered as platform tools.
- **Known-answer tests.** Every metric in a template documents its convention and has
  known-answer tests. Every profile, with its default components, passes its own `uv sync`,
  ruff, mypy, and pytest.
- **No trading.** Templates contain no live trading, order routing, or broker connectivity.
- **Template authoring is split.**
  - Generic pack authoring is pytemplate-uv's own roadmap and is not planned in this
    repository.
  - App Factory adds quant-specific authoring: fork a baseline profile, add questions and
    settings, validate, and register the result in the hub.
  - If pytemplate-uv ships generic authoring, App Factory hands it the generic layout.

## Consequences

- Users get a working, tested project in minutes, and own all of its code.
- The templates need maintenance as conventions and dependencies move. They are versioned
  with the pack, and generated projects can update through `pytemplate update`.
- Premium or firm-specific packs, such as factor libraries and data pipelines (section 6 of
  the engineering plan), build on the same pack format.
