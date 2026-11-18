---
title: Strategy memo - government curve trades
authors: Macro desk, Kestrel Point Capital (fictional)
published: 2024-05-20
license: CC0 synthetic fixture
---

# Strategy memo: government curve trades

## Thesis

Most of the variation in government yield curves is explained by three components found by principal
component analysis: level, slope, and curvature. PCA on the US Treasury 2Y and 10Y futures, the Bund,
and the Gilt shows level explains about 85% of daily variance. Curve trades isolate the slope.

## Trades

- **US 2s10s steepener:** long the 2Y future against short the 10Y future, duration-weighted so the
  level component nets to roughly zero. The 2Y moves about a third as much as the 10Y.
- **Bund versus Gilt:** relative value between the two long-end contracts when the spread is more than
  two standard deviations from its one-year mean.

## Sizing and limits

Duration-neutral sizing means large gross notionals: the 2Y leg can be three times the 10Y leg. Gross
exposure therefore overstates risk on this desk; VaR and the rates shock scenario are the right
measures. The risk committee's +100bp parallel shock should cost a duration-neutral steepener little.

## Risks

A bear flattener driven by the front end, and liquidity in the Gilt future around fiscal events.
