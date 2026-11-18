---
title: Strategy memo - G10 FX carry
authors: Macro desk, Kestrel Point Capital (fictional)
published: 2024-02-12
license: CC0 synthetic fixture
---

# Strategy memo: G10 FX carry

## Thesis

FX carry buys high-yielding G10 currencies against low-yielding ones and earns the interest rate
differential, the carry, as long as spot does not move against the position by more. Across the eight
pairs the macro desk trades, the widest differentials in 2024 were long USDJPY and long USDCHF, and the
narrowest short EURUSD.

## How we trade it

- Signal: annualized carry from forward points, z-scored across the pairs each day and winsorized at
  three standard deviations.
- Sizing: inverse volatility, so a pair's weight is its signal times 3% over its annual volatility,
  capped at 1.5 times desk capital per pair.
- Rebalance weekly. Turnover is low because carry moves slowly.

## Risks

Carry is short volatility. When risk appetite falls, funding currencies such as the yen and the franc
rally and the trade gives back months of carry in days. Crowding makes this worse: when many funds hold
the same carry trades, the unwind is faster. We expect a Sharpe ratio near 0.4 before costs, and a
maximum drawdown that can exceed one year of expected return.

## What would change our mind

If the desk's realized information coefficient for carry stays indistinguishable from zero over a full
year, we cut its risk budget. See the research note on the carry signal review.
