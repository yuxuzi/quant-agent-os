---
title: Strategy memo - commodity trend following
authors: Commodities desk, Kestrel Point Capital (fictional)
published: 2024-03-04
license: CC0 synthetic fixture
---

# Strategy memo: commodity trend following

## Thesis

Time-series momentum in futures markets is one of the most persistent effects in the literature: a
contract that has risen over the past months tends to keep rising, and one that has fallen tends to keep
falling. Managed futures funds have earned it for decades. The commodities desk runs a trend signal on
six contracts: WTI, Brent, natural gas, gold, copper, and silver.

## How we trade it

- Signal: the fund's production momentum signal, a z-score refreshed daily by research.
- Sizing: each contract targets 4% annual volatility, capped at 40% of desk capital.
- Rebalance every five business days. Roll yield is tracked but not traded on this desk.

## Expectations

Trend following earns most of its return in a few long moves and bleeds in choppy markets. Expect a hit
rate under 50% and a Sharpe ratio near 0.5. Natural gas carries the largest single-contract risk even
at a small weight because its volatility is near 60% a year.

## Risks

Sharp reversals, such as an OPEC surprise, hurt a trend book before the signal turns. The oil supply
shock scenario in the risk committee's set is the stress we watch.
