---
title: Research note - momentum signal IC review
authors: Research team, Kestrel Point Capital (fictional)
published: 2025-01-15
license: CC0 synthetic fixture
---

# Momentum signal: IC review for 2023-2024

## Result

The production momentum signal has a mean daily rank IC of about 0.025 across all 40 instruments,
with a t-statistic above 3. That is modest but reliable, which is what a momentum signal should be.
The IC information ratio, mean over standard deviation of the daily IC, is about 0.15.

## Method

Rank IC is the Spearman correlation across instruments between the signal on day t and the total
return on day t+1, averaged over days. We used walk-forward validation, refitting nothing in the
test window, and quintile sorts as a check: the top quintile beats the bottom by a small but steady
margin.

## Decay

The signal is persistent, so the IC decays slowly with the horizon; weekly rebalancing loses little.

## Recommendation

Keep momentum as the core signal on all three desks. Report turnover and transaction costs with any
backtest, because a weekly-rebalanced momentum book trades more than the carry book.
