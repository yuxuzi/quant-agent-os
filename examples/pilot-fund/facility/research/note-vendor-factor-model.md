---
title: Research note - using the vendor factor model
authors: Risk team, Kestrel Point Capital (fictional)
published: 2024-07-01
license: CC0 synthetic fixture
---

# Using the vendor factor model

The vendor delivers a five-factor model at each month end: equity market, rates, US dollar,
commodity, and credit spread. Loadings are per instrument; the factor covariance and specific variances
are annualized. The rates factor is positive when bond prices rise; the credit spread factor is positive
when spreads tighten.

A desk's factor exposures are its market values times the loadings. Its model variance is the
exposures' quadratic form in the factor covariance plus the sum of squared market values times specific
variance. Use the latest month-end model on or before the risk date, never a later one.

The equity long/short desk is close to market-neutral, so most of its model risk is specific. The
macro desk's risk is dominated by the dollar factor because its FX positions all load on it.
