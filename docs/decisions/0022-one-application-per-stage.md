# 0022: One application per stage of the investment process

- Status: accepted (direction from the owner, 2026-11-07)
- Date: 2026-11-07
- Amends: [0009](0009-quant-baseline-templates.md)

## Context

`quant-lab` puts study, backtest, and report in one project. That is a good demonstration, and the wrong
shape for a firm: different people do these jobs, with different data and different rights. Someone who
wants a monthly return table should not receive a signal research lab.

We compared three groupings:
- **By team** (research, trading, risk, operations). Team boundaries differ between firms and change with
  the organization chart, so the templates would too.
- **By file exchanged.** Precise, and too abstract for a person choosing what to build.
- **By stage of the investment process, split only where the input, the owner, or the sensitivity of the data
  changes.** Stable across firms, and it names the work people do. We chose this one.

## Decision

- **A `quant-apps` pack ships four applications,** each its own profile:

  | Application | Used by | Takes | Produces |
  | --- | --- | --- | --- |
  | `alpha-research` | Quant researchers | Prices, the firm's signal | IC, decay, spreads; a signal file |
  | `strategy-backtest` | Researchers, PMs | Signals or target weights, prices | Net and gross returns, weights |
  | `risk-monitor` | Risk, PMs | Positions, prices | Exposures, value at risk, stress, limit checks |
  | `performance-report` | COO, investor relations, PMs | Returns | Horizon tables, risk basics, tear sheets, reconciliation |

- **Risk and performance are separate,** because they differ on the input: positions are sensitive and read
  daily, while returns go to investors. Return-based risk measures appear in both.
- **Two stages are deliberately not templates.** Research knowledge is the platform's Quant Research product.
  Execution is left out, because the product is read-only.
- **The stages exchange ordinary files:** a date column, then one numeric column per name, with provenance in
  a `.meta.json` file beside it. So a firm can replace any stage with a system that writes the same file.
- **A generated application works as generated, and is meant to be changed.** It offers four levels:
  settings; choosing report sections; adding code in a `firm/` folder that template updates skip; and changing
  anything else, with known-answer tests to catch mistakes. Each project has an `AGENTS.md` for coding assistants.
- **The firm supplies the alpha.** Templates provide the harness around a signal. A firm's named signal is tested
  for look-ahead before each run.
- **Generated command lines use Typer,** like `qaos` and pytemplate-uv's own profiles.
- **The pack is MIT, inside the wheel,** as [decision 0019](0019-business-source-license.md) sets for packs, so
  generated code carries no platform terms. `quant-lab` stays as the all-in-one sample.

## Consequences

- `metrics.py` is copied into each project so that it stays portable. `tests/factory/test_quant_apps.py` holds
  every copy byte-identical to the baseline's, along with the other shared files, and holds the new risk measures
  to the platform's.
- Excel input and output add `openpyxl` to `performance-report` projects, not to the platform.
- Generated projects still cannot receive fixes through `pytemplate update`, because built-in packs record a local
  path with no tags. `LIMITATIONS.md` says so. This remains open.
- The Excel reader was tested on workbooks built to resemble administrator statements, not on real ones.
