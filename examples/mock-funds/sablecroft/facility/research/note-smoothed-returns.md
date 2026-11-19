---
title: Research note - smoothed returns and illiquid managers
authors: Risk, Sablecroft Alternatives (fictional)
published: 2025-08-04
license: CC0 synthetic fixture
---

# Smoothed returns

Specialty credit and real-asset managers mark positions with a lag, so their reported monthly returns are
autocorrelated and their volatility is understated. We model the reported return as a weighted sum of the
current and two previous true returns, with weights that add to one (Getmansky, Lo and Makarov, 2004).

For Wrenfield Specialty Credit the weights are about 0.52, 0.30, and 0.18, so true volatility is about 1.6 times
the reported figure. The Sharpe ratio of such managers is adjusted for autocorrelation with Lo's factor before it
is compared with liquid peers.
