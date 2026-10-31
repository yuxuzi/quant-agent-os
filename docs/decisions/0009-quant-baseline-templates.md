# 0009: Quant baseline templates ship with App Factory as portable, bias-tested apps

- Status: proposed
- Date: 2026-10-31

## Context

Most quant projects start the same way: research a signal, backtest it, and report the result.
Quant-Agent-OS can encode the conventions so each project need not rediscover them:
- metric definitions and annualisation;
- costs;
- walk-forward evaluation;
- keeping facts apart from interpretation.

A template that quietly leaks future data does more harm than no template, because it looks
finished. Tests that metric formulas are correct do not catch that.

## Decision

- **A `quant-baseline` pack ships in `quant_app_factory/packs/`.**
  - Its profiles are `alpha-research`, `backtest`, and `report`.
  - Its components are `metrics` (required), `data-csv` (the default), `data-parquet`,
    `data-database`, and `agent-tools`.
- **One source for metric code.** The required `metrics` component holds every metric for all
  three profiles, with documented conventions and known-answer tests.
- **Bias tests in every generated project:**
  - a forward-shifted signal must change the result;
  - trades lag the signal by one bar by default;
  - costs order returns correctly.
  - A `LIMITATIONS.md` lists what the template does not handle: survivorship, point-in-time
    fundamentals, corporate actions, and borrow.
- **Portable.** The generated apps do not depend on Quant-Agent-OS. The optional
  `agent-tools` component adds typed `@tool` wrappers and `check_tool` tests, with a
  deliberate dependency on `quant-agent-os`.
- **No trading.** There is no order routing, live trading, or broker code.
- **Template authoring.**
  - `quant-factory template fork` copies a baseline profile into a user pack, which the user
    edits by hand or with Claude Code.
  - `template check` validates the pack.
  - Generic pack authoring is pytemplate-uv's own roadmap.

## Consequences

- Users get a tested project in minutes and own its code.
- **Fixes don't flow back.** A metric fix reaches new projects; existing ones update through
  `pytemplate update`. If the analytics package (B-13) ships, `metrics` may become a
  dependency on it rather than generated code. That would be a later, deliberate change.
- **Testing grows with each component.** Every combination of components a profile offers
  needs a render test, and the default set needs a smoke test.
