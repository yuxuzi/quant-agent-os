---
title: Research note - volatility targeting
authors: Research team, Kestrel Point Capital (fictional)
published: 2024-09-10
license: CC0 synthetic fixture
---

# Volatility targeting across desks

Each desk scales positions to inverse volatility, then scales the whole book to a 10% annual volatility
target using a 63-day window, with leverage capped at three. This is close to risk parity at the
position level and keeps drawdowns comparable across desks.

Two cautions. First, a short window reacts late to a volatility spike and early to its decay, which
raises turnover. Second, the leverage cap binds on the macro desk, whose 2Y Treasury position has very
low volatility, so its realized volatility runs below target. Report realized volatility and turnover
with any vol-targeted backtest.
