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
  - Its one profile is `quant-research` (updated 2026-11-01, B-28 S1): a single project with
    `study`, `backtest`, and `report` commands that share data, signals, metrics, one
    trade-timing convention, and a `config.toml` of research parameters. The 1.x profiles
    `alpha-research`, `backtest`, and `report` are aliases for one release. Three projects for
    one workflow meant copying signal code between them and keeping three environments, and
    the report profile was too small to stand alone.
  - Its components are `metrics` (required), `data-csv` (the default), `data-parquet`,
    `data-database`, and `agent-tools`.
- **One source for metric code.** The required `metrics` component holds every metric, with
  documented conventions and known-answer tests.
- **Bias tests in every generated project:**
  - changing future prices never changes a past signal or a past return (no look-ahead);
  - a forward-shifted signal must change the result;
  - trades lag the signal by one bar by default, and studies measure returns from the same
    trade bar (`config.TRADE_LAG`), so a study never promotes an edge the backtest cannot
    earn;
  - costs order returns correctly.
  - missing prices are honest: gaps are bridged, and delisted positions lose
    `config.DELISTING_RETURN` once;
  - holdings drift between scheduled rebalances, and costs are charged on actual trades;
  - IC t-statistics use Newey-West standard errors;
  - signals default to the standard definitions (12-1 momentum, one-month reversal);
  - a random walk shows no edge across 40 seeds.
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
